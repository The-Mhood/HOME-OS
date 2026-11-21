// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HomeOsIosCompanion",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "HomeOsIosCompanion", targets: ["HomeOsIosCompanion"])
    ],
    targets: [
        .target(
            name: "HomeOsIosCompanion",
            path: "Sources/HomeOsCompanion"
        )
    ]
)
