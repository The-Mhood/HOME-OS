// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HomeOsMacAgent",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "homeos-mac-agent", targets: ["HomeOsMacAgent"])
    ],
    targets: [
        .executableTarget(
            name: "HomeOsMacAgent",
            path: "Sources/HomeOsMacAgent"
        )
    ]
)
