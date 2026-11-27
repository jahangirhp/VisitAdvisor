import Foundation
import XCTest
@testable import VisitAdvisorKit

/// Fixtures are SYNTHETIC (scripts/ios_golden_fixtures.py): random vectors, no museum data.
func fixtureURL(_ name: String) -> URL {
    Bundle.module.url(forResource: "Fixtures", withExtension: nil)!.appendingPathComponent(name)
}

func copiedFixturePackage() throws -> URL {
    let target = FileManager.default.temporaryDirectory
        .appendingPathComponent("pkg-\(UUID().uuidString)")
    try FileManager.default.copyItem(at: fixtureURL("synthetic-package"), to: target)
    return target
}

final class VenuePackageTests: XCTestCase {
    func testSyntheticPackageValidates() throws {
        let package = try VenuePackage(directory: fixtureURL("synthetic-package"))
        XCTAssertEqual(package.manifest.exhibitCount, 6)
        XCTAssertEqual(package.matrix.rows, package.manifest.embeddingSpaces[0].rowCount)
        XCTAssertEqual(package.index.rows.count, package.matrix.rows)
        XCTAssertFalse(package.policy.calibrated)
    }

    func testSameSizeCorruptionFailsChecksum() throws {
        let root = try copiedFixturePackage()
        let url = root.appendingPathComponent("vectors/dinov2-small-cls.f32")
        var data = try Data(contentsOf: url)
        data[100] ^= 1
        try data.write(to: url)
        XCTAssertThrowsError(try VenuePackage(directory: root)) {
            XCTAssertEqual($0 as? VenuePackageError, .checksumMismatch)
        }
    }

    func testTruncatedFileFailsSize() throws {
        let root = try copiedFixturePackage()
        try Data(count: 8).write(to: root.appendingPathComponent("vectors/dinov2-small-cls.f32"))
        XCTAssertThrowsError(try VenuePackage(directory: root)) {
            XCTAssertEqual($0 as? VenuePackageError, .sizeMismatch)
        }
    }

    func testPathTraversalIsRejected() throws {
        let root = URL(fileURLWithPath: "/tmp/package")
        for path in ["../x", "/etc/passwd", "a/../../x", "a\\b", ""] {
            XCTAssertThrowsError(try VenuePackage.resolve(path, in: root)) {
                XCTAssertEqual($0 as? VenuePackageError, .unsafePath)
            }
        }
        XCTAssertNoThrow(try VenuePackage.resolve("vectors/a.f32", in: root))
    }

    func testIncompatibleContractIsRejected() throws {
        let root = try copiedFixturePackage()
        let url = root.appendingPathComponent("manifest.json")
        let text = try String(contentsOf: url, encoding: .utf8)
            .replacingOccurrences(of: VenueManifest.pipelineContractID, with: "other-pipeline")
        try text.write(to: url, atomically: true, encoding: .utf8)
        XCTAssertThrowsError(try VenuePackage(directory: root)) {
            XCTAssertEqual($0 as? VenuePackageError, .incompatible("manifest"))
        }
    }
}
