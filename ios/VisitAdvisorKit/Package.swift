// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VisitAdvisorKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "VisitAdvisorKit", targets: ["VisitAdvisorKit"]),
    ],
    targets: [
        .target(name: "VisitAdvisorKit"),
        .testTarget(
            name: "VisitAdvisorKitTests",
            dependencies: ["VisitAdvisorKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
