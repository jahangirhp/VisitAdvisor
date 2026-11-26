import CryptoKit
import Foundation
import XCTest
@testable import VisitAdvisorKit

private struct PreprocessingGolden: Decodable {
    struct Input: Decodable { let file: String; let width: Int; let height: Int }
    let synthetic: Bool
    let input: Input
    let frame512_sha256: String
    let region_box: [Double]
    let region512_sha256: String
    let proposal224_bilinear_sha256: String
    let model_input_float32le_sha256: String
    let model_input_first_values: [Float]
}

private func sha(_ bytes: [UInt8]) -> String {
    SHA256.hash(data: Data(bytes)).map { String(format: "%02x", $0) }.joined()
}

/// The phone must feed the model exactly what the server's Pillow/NumPy path does.
final class PreprocessingTests: XCTestCase {
    func testCameraPathIsBitExactWithServer() throws {
        let golden = try JSONDecoder().decode(
            PreprocessingGolden.self, from: Data(contentsOf: fixtureURL("preprocessing-golden.json")))
        XCTAssertTrue(golden.synthetic)
        let raw = try [UInt8](Data(contentsOf: fixtureURL(golden.input.file)))
        let image = RGBImage(width: golden.input.width, height: golden.input.height, pixels: raw)

        let frame = CameraPreprocessing.frame(from: image)
        XCTAssertEqual(sha(frame.pixels), golden.frame512_sha256, "frame resample")

        let box = NormalizedBox(
            x0: Float(golden.region_box[0]), y0: Float(golden.region_box[1]),
            x1: Float(golden.region_box[2]), y1: Float(golden.region_box[3]))
        let region = CameraPreprocessing.crop(frame, box: box)
        XCTAssertEqual(sha(region.pixels), golden.region512_sha256, "region crop")

        let proposal = PillowResample.resize(frame, width: 224, height: 224, filter: .bilinear)
        XCTAssertEqual(sha(proposal.pixels), golden.proposal224_bilinear_sha256, "proposal input")

        let tensor = CameraPreprocessing.modelInput(region)
        XCTAssertEqual(Array(tensor.prefix(8)), golden.model_input_first_values)
        let bytes = tensor.flatMap { value -> [UInt8] in
            withUnsafeBytes(of: value.bitPattern.littleEndian) { Array($0) }
        }
        XCTAssertEqual(sha(bytes), golden.model_input_float32le_sha256, "model tensor")
    }

    func testFlatFrameIsUnreadable() {
        let flat = RGBImage(width: 8, height: 8, pixels: [UInt8](repeating: 128, count: 192))
        XCTAssertTrue(CameraPreprocessing.isUnreadable(flat))
        var noisy = flat
        for i in stride(from: 0, to: 192, by: 6) { noisy.pixels[i] = 0 }
        XCTAssertFalse(CameraPreprocessing.isUnreadable(noisy))
    }
}
