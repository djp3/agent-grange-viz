// swift-tools-version:5.9
// Copyright (C) 2026 Don Patterson
// SPDX-License-Identifier: GPL-3.0-or-later
// The native shell of agent-grange-viz: a window hosting a SwiftTerm terminal that runs the
// PyInstaller-built Python TUI (agent-grange-viz-core).
// scripts/build_app.sh builds this and assembles dist/agent-grange-viz.app around it.
//
// There is no Sparkle here yet, unlike agent-farm and agent-frolic: this first cut is built
// and run on the Mac it was built on, and does not update itself (see README.md).
import PackageDescription

let package = Package(
    name: "AgentGrangeViz",
    // The same as LSMinimumSystemVersion in Info.plist. It follows the libraries bundled with
    // the Python side (Homebrew builds them for the macOS they were built on);
    // scripts/check_macos_minimum.sh stops the build when anything in the app needs more.
    platforms: [.macOS("27.0")],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", from: "1.99.3"),
    ],
    targets: [
        .executableTarget(
            name: "AgentGrangeViz",
            dependencies: [
                .product(name: "SwiftTerm", package: "SwiftTerm"),
            ],
            path: "Sources/AgentGrangeViz"
        ),
    ]
)
