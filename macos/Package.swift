// swift-tools-version:5.9
// Copyright (C) 2026 Don Patterson
// SPDX-License-Identifier: GPL-3.0-or-later
// The native shell of agent-grange-viz: a window hosting a SwiftTerm terminal that runs the
// PyInstaller-built Python TUI (agent-grange-viz-core).
// scripts/build_app.sh builds this and assembles dist/agent-grange-viz.app around it.
//
// There is no Sparkle here yet, unlike agent-farm and agent-frolic: the app does not update
// itself, and a new release is a new download (see README.md).
import PackageDescription

let package = Package(
    name: "AgentGrangeViz",
    // The same as LSMinimumSystemVersion in Info.plist. It follows the libraries bundled with
    // the Python side (Homebrew builds them for the macOS they were built on). The build stops
    // (scripts/check_min_macos.py) when anything in the app needs more than Info.plist says,
    // or when the executable this makes records another minimum than Info.plist's.
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
