// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VisitAdvisorKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "VisitAdvisorKit", targets: ["VisitAdvisorKit"]),
    ],
    targets: [
        // Pixel loops are unusable unoptimised; optimise Debug builds too.
        .target(name: "VisitAdvisorKit", swiftSettings: [.unsafeFlags(["-O"], .when(configuration: .debug))]),
        .testTarget(
            name: "VisitAdvisorKitTests",
            dependencies: ["VisitAdvisorKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
