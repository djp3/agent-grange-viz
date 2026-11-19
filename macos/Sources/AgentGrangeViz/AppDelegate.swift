// Copyright (C) 2026 Don Patterson
// SPDX-License-Identifier: GPL-3.0-or-later
import AppKit
import SwiftTerm

/// Colors and font for the terminal view. Textual paints every cell itself, so the
/// native colors only show before the viewer starts and in the exit message. They are the
/// viewer's own plane and ink (theme.py).
enum Theme {
    static let background = NSColor(srgbRed: 0.051, green: 0.051, blue: 0.051, alpha: 1)
    static let foreground = NSColor(srgbRed: 0.941, green: 0.937, blue: 0.925, alpha: 1)
    static let defaultFontSize: CGFloat = 13

    /// The chosen font if it is installed, else SF Mono, Menlo, or the system monospace.
    static func font(named name: String?, size: CGFloat) -> NSFont {
        if let name, let f = NSFont(name: name, size: size) { return f }
        for fallback in ["SFMono-Regular", "Menlo-Regular"] {
            if let f = NSFont(name: fallback, size: size) { return f }
        }
        return NSFont.monospacedSystemFont(ofSize: size, weight: .regular)
    }
}

/// The terminal view, taking the click that brings the window forward as a click too. AppKit
/// spends the first click on a window in the background on activating it, so picking a column
/// from another app would take two clicks; this way the viewer gets that first one.
final class FirstClickTerminalView: LocalProcessTerminalView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Hosts the Python TUI (agent-grange-viz-core, a PyInstaller build of main.py) inside a
/// SwiftTerm view. Everything the viewer does still happens in Python; this file only
/// provides the window, the pty and the menus.
///
/// Unlike agent-farm and agent-frolic there is no Sparkle here yet: the app does not update
/// itself, and a new release is a new download.
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, LocalProcessTerminalViewDelegate {
    /// The bundle's release number ("0.1.0"); the viewer gets it as AGENT_GRANGE_VIZ_VERSION.
    private let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    /// Shown until the viewer sets a title of its own (via OSC 0) and whenever it clears it.
    private var defaultTitle: String {
        guard let v = version, !v.isEmpty else { return "📡 agent-grange-viz" }
        return "📡 agent-grange-viz v\(v)"
    }
    /// Draws the window's title in the middle of its bar, whenever it is set (CenteredTitle).
    private var centeredTitle: CenteredTitle?
    private var window: NSWindow!
    private var terminal: LocalProcessTerminalView!
    private var coreRunning = false
    /// Set by Restart Viewer and by Show the Demo; consumed when the old process has actually gone away.
    private var restartRequested = false
    /// Whether the viewer runs its made-up team (`--demo`) instead of reading this Mac's grange.
    /// Never remembered: a demo left on would show a team that does not exist the next morning.
    private var demo = false
    private weak var demoItem: NSMenuItem?

    private let defaults = UserDefaults.standard
    private var fontSize: CGFloat {
        get { let v = defaults.double(forKey: "fontSize"); return v > 0 ? CGFloat(v) : Theme.defaultFontSize }
        set { defaults.set(Double(newValue), forKey: "fontSize") }
    }
    private var fontName: String? {
        get { defaults.string(forKey: "fontName") }
        set { defaults.set(newValue, forKey: "fontName") }
    }
    /// SwiftTerm's font smoothing (on = standard macOS rendering, off = thin strokes like iTerm2's setting).
    private var fontSmoothing: Bool {
        get { defaults.object(forKey: "fontSmoothing") as? Bool ?? true }
        set { defaults.set(newValue, forKey: "fontSmoothing") }
    }
    private var currentFont: NSFont { Theme.font(named: fontName, size: fontSize) }
    private weak var fontMenu: NSMenu?

    static let logDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/agent-grange-viz")
    static let dataDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/agent-grange-viz")

    // MARK: - lifecycle

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenus()
        makeWindow()
        // Page Up and Page Down reach the viewer only once it asks for application cursor
        // keys: until then SwiftTerm keeps them for its own scrollback.
        startCore()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        if coreRunning { terminal.terminate() }
    }

    private func makeWindow() {
        // 160 cells wide and 48 high at the default text size: the grid is drawn for 150 by
        // 46, and a dozen teammates fit without scrolling. On a narrower screen macOS fits
        // the window to it, and the grid scrolls.
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800),
                         styleMask: [.titled, .closable, .miniaturizable, .resizable],
                         backing: .buffered, defer: false)
        w.title = defaultTitle
        centeredTitle = CenteredTitle(in: w)
        w.minSize = NSSize(width: 600, height: 300)
        w.tabbingMode = .disallowed
        w.backgroundColor = Theme.background
        w.delegate = self
        w.center()
        w.setFrameAutosaveName("agent-grange-viz.main")

        let t = FirstClickTerminalView(frame: w.contentView!.bounds)
        t.autoresizingMask = [.width, .height]
        t.processDelegate = self
        t.nativeBackgroundColor = Theme.background
        t.nativeForegroundColor = Theme.foreground
        t.font = currentFont
        t.fontSmoothing = fontSmoothing
        w.contentView?.addSubview(t)
        terminal = t
        window = w

        w.makeKeyAndOrderFront(nil)
        w.makeFirstResponder(t)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - the Python core

    /// True for `swift run` during development: only the assembled bundle has an
    /// Info.plist, and with it a bundle identifier.
    private var isDevelopmentRun: Bool { Bundle.main.bundleIdentifier == nil }

    private func coreExecutable() -> String? {
        // AGENT_GRANGE_VIZ_CORE lets a `swift run` during development point at any executable.
        // The assembled app ignores it: the environment must not choose what the app runs.
        if isDevelopmentRun, let p = ProcessInfo.processInfo.environment["AGENT_GRANGE_VIZ_CORE"], !p.isEmpty {
            return p
        }
        guard let res = Bundle.main.resourceURL else { return nil }
        let p = res.appendingPathComponent("agent-grange-viz-core/agent-grange-viz-core").path
        return FileManager.default.isExecutableFile(atPath: p) ? p : nil
    }

    private func childEnvironment() -> [String] {
        // TERM, COLORTERM=truecolor (Textual needs it for 24-bit color), LANG, HOME, USER...
        var env = Terminal.getEnvironmentVariables(termName: "xterm-256color", trueColor: true)
        let mine = ProcessInfo.processInfo.environment
        // The viewer runs no other program, so the child only needs the PATH it was launched with.
        env.append("PATH=" + (mine["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"))
        env.append("TERM_PROGRAM=agent-grange-viz")
        if let v = version {
            env.append("AGENT_GRANGE_VIZ_VERSION=\(v)")
        }
        // Where Docker listens, when whoever started the app said so. Started from the Finder
        // nobody did, and the viewer looks in the usual places (settings.py: docker_socket).
        if let docker = mine["DOCKER_HOST"], !docker.isEmpty {
            env.append("DOCKER_HOST=\(docker)")
        }
        return env
    }

    private func startCore() {
        guard let exe = coreExecutable() else {
            terminal.feed(text: "\r\n  agent-grange-viz-core was not found inside the app bundle.\r\n")
            return
        }
        terminal.feed(text: "\u{1b}[2J\u{1b}[H")
        coreRunning = true
        terminal.startProcess(executable: exe, args: demo ? ["--demo"] : [], environment: childEnvironment(),
                              execName: "agent-grange-viz-core")
    }

    @objc func restartCore(_ sender: Any?) {
        guard coreRunning else { startCore(); return }
        // Only stop it here. The viewer exits cleanly on SIGTERM, and SwiftTerm ignores a
        // new startProcess until the old session has wound down, so the relaunch happens in
        // processTerminated once that is true.
        restartRequested = true
        terminal.terminate()
    }

    /// Switch between this Mac's grange and the made-up team, by starting the viewer again
    /// with or without `--demo`.
    @objc func toggleDemo(_ sender: Any?) {
        demo.toggle()
        demoItem?.state = demo ? .on : .off
        restartCore(sender)
    }

    // MARK: - LocalProcessTerminalViewDelegate

    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}

    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {
        DispatchQueue.main.async { [weak self] in self?.window.title = title.isEmpty ? self?.defaultTitle ?? "" : title }
    }

    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}

    func processTerminated(source: TerminalView, exitCode: Int32?) {
        coreRunning = false
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if self.restartRequested {       // Restart Viewer, or the demo switched: the exit was ours
                self.restartRequested = false
                self.startCore()
                return
            }
            if exitCode == 0 {               // the user pressed q inside the viewer
                NSApp.terminate(nil)
                return
            }
            let code = exitCode.map(String.init) ?? "?"
            let log = Self.logDir.appendingPathComponent("agent-grange-viz.log").path
            self.terminal.feed(text: "\r\n\u{1b}[31m  agent-grange-viz-core exited with code \(code)\u{1b}[0m\r\n"
                               + "  details: \(log)\r\n  \u{2318}R restarts the viewer, \u{2318}Q quits.\r\n")
        }
    }

    // MARK: - menus

    private func buildMenus() {
        let bar = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About agent-grange-viz",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide agent-grange-viz", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "Hide Others",
                                         action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit agent-grange-viz", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        bar.addItem(item("agent-grange-viz", appMenu))

        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        bar.addItem(item("Edit", edit))

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Bigger Text", action: #selector(biggerText(_:)), keyEquivalent: "+")
        view.addItem(withTitle: "Smaller Text", action: #selector(smallerText(_:)), keyEquivalent: "-")
        view.addItem(withTitle: "Default Text Size", action: #selector(resetText(_:)), keyEquivalent: "0")
        view.addItem(.separator())
        view.addItem(item("Font", buildFontMenu()))
        let smoothing = view.addItem(withTitle: "Font Smoothing", action: #selector(toggleSmoothing(_:)), keyEquivalent: "")
        smoothing.state = fontSmoothing ? .on : .off
        bar.addItem(item("View", view))

        let viewer = NSMenu(title: "Viewer")
        viewer.addItem(withTitle: "Restart Viewer", action: #selector(restartCore(_:)), keyEquivalent: "r")
        // The made-up team of thirteen and its four-minute loop, for seeing every state the
        // viewer can draw without waiting for a real mesh to break.
        let demoEntry = viewer.addItem(withTitle: "Show the Demo", action: #selector(toggleDemo(_:)), keyEquivalent: "d")
        demoEntry.state = demo ? .on : .off
        demoItem = demoEntry
        viewer.addItem(.separator())
        viewer.addItem(withTitle: "Open Log Folder", action: #selector(openLogs(_:)), keyEquivalent: "")
        viewer.addItem(withTitle: "Reveal Settings File", action: #selector(revealSettings(_:)), keyEquivalent: "")
        bar.addItem(item("Viewer", viewer))

        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.zoom(_:)), keyEquivalent: "")
        bar.addItem(item("Window", win))
        NSApp.windowsMenu = win

        let help = NSMenu(title: "Help")
        help.addItem(withTitle: "agent-grange-viz on GitHub", action: #selector(openGitHub(_:)), keyEquivalent: "")
        help.addItem(withTitle: "agent-grange on GitHub", action: #selector(openGrange(_:)), keyEquivalent: "")
        bar.addItem(item("Help", help))
        NSApp.helpMenu = help

        NSApp.mainMenu = bar
    }

    private func item(_ title: String, _ menu: NSMenu) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        i.submenu = menu
        return i
    }

    /// Every fixed-pitch family installed on this Mac, the current one checked.
    private func buildFontMenu() -> NSMenu {
        let menu = NSMenu(title: "Font")
        let manager = NSFontManager.shared
        let current = currentFont.familyName
        for family in manager.availableFontFamilies.sorted() {
            guard let f = manager.font(withFamily: family, traits: [], weight: 5, size: 13), f.isFixedPitch else { continue }
            let entry = NSMenuItem(title: family, action: #selector(chooseFont(_:)), keyEquivalent: "")
            entry.representedObject = f.fontName
            entry.state = family == current ? .on : .off
            menu.addItem(entry)
        }
        fontMenu = menu
        return menu
    }

    @objc func chooseFont(_ sender: NSMenuItem) {
        guard let name = sender.representedObject as? String else { return }
        fontName = name
        terminal.font = currentFont
        fontMenu?.items.forEach { $0.state = $0 === sender ? .on : .off }
    }

    @objc func toggleSmoothing(_ sender: NSMenuItem) {
        fontSmoothing.toggle()
        terminal.fontSmoothing = fontSmoothing
        terminal.needsDisplay = true
        sender.state = fontSmoothing ? .on : .off
    }

    @objc func biggerText(_ sender: Any?) { setFontSize(fontSize + 1) }
    @objc func smallerText(_ sender: Any?) { setFontSize(max(8, fontSize - 1)) }
    @objc func resetText(_ sender: Any?) { setFontSize(Theme.defaultFontSize) }

    private func setFontSize(_ size: CGFloat) {
        fontSize = size
        terminal.font = currentFont   // SwiftTerm resizes the pty; Textual reflows
    }

    @objc func openLogs(_ sender: Any?) {
        try? FileManager.default.createDirectory(at: Self.logDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(Self.logDir)
    }

    @objc func revealSettings(_ sender: Any?) {
        try? FileManager.default.createDirectory(at: Self.dataDir, withIntermediateDirectories: true)
        let file = Self.dataDir.appendingPathComponent("settings.json")
        if FileManager.default.fileExists(atPath: file.path) {
            NSWorkspace.shared.activateFileViewerSelecting([file])
        } else {
            NSWorkspace.shared.open(Self.dataDir)
        }
    }

    @objc func openGitHub(_ sender: Any?) {
        NSWorkspace.shared.open(URL(string: "https://github.com/djp3/agent-grange-viz")!)
    }

    @objc func openGrange(_ sender: Any?) {
        NSWorkspace.shared.open(URL(string: "https://github.com/djp3/agent-grange")!)
    }
}
