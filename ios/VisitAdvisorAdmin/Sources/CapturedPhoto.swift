import Accelerate
import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import UIKit
import UniformTypeIdentifiers
import Vision

/// Angles the guided capture asks for (server `requested_view` values).
/// Reference photos the server accepts per object (MAX_VIEWS on the server).
let maxPhotos = 4

enum CaptureAngle: String, CaseIterable, Identifiable, Codable {
    case front, left, right, back
    case obliqueLeft = "oblique-left", obliqueRight = "oblique-right", elevated
    var id: String { rawValue }

    var label: String {
        switch self {
        case .front: "Front"
        case .left: "Left side"
        case .right: "Right side"
        case .back: "Back"
        case .obliqueLeft: "Front-left (45°)"
        case .obliqueRight: "Front-right (45°)"
        case .elevated: "Slightly from above"
        }
    }

    static func plan(planar: Bool) -> [CaptureAngle] {
        // The server keeps at most `maxPhotos` reference photos per object.
        planar ? [.front, .obliqueLeft, .obliqueRight] : [.front, .obliqueLeft, .obliqueRight, .back]
    }
}

/// Server recognition profile for an object type (mirrors `profile()` on the server).
func isPlanar(_ type: String) -> Bool {
    ["painting", "drawing", "print", "photograph"].contains(type.lowercased())
}

/// Capture profile sent with each photo: rooms and buildings are scenes (the
/// server always uses their full photo), flat artworks planar, the rest 3D.
func captureProfile(_ type: String) -> String {
    let type = type.lowercased()
    if ["room", "building"].contains(type) { return "scene" }
    return isPlanar(type) ? "planar" : "multiview"
}

/// One captured reference photo: the untouched original (upright JPEG) plus an
/// optional on-device foreground mask. The original is always uploaded; the
/// mask is a separate file, so both versions stay available on the server.
struct CapturedPhoto: Identifiable {
    let id = UUID()
    let view: CaptureAngle
    let profile: String  // "planar" | "multiview"
    let jpeg: Data
    let preview: UIImage
    let maskPNG: Data?
    let cutout: UIImage?
    let targetBox: [Double]  // normalized x0, y0, x1, y1 of the mask (full frame without one)
    let quality: (sharpness: Double, clipped: Double, target: Double)
    let sessionID: String
    var useMask: Bool

    var hasMask: Bool { maskPNG != nil }

    /// `guided-capture-v3` details. A mask is recorded as a reviewed
    /// `device-mask` with its Apple Vision identity; without one, the photo is a
    /// reviewed `full-frame` selection (background kept).
    func metadataJSON() throws -> Data {
        let masked = useMask && maskPNG != nil
        var selection: [String: Any] = [
            "method": masked ? "device-mask" : "full-frame",
            "reviewed": true,
            "source_sha256": sha256(jpeg),
        ]
        var capture: [String: Any] = [
            "version": "guided-capture-v3",
            "session_id": sessionID,
            "frame_id": id.uuidString.replacingOccurrences(of: "-", with: "").lowercased(),
            "profile": profile,
            "requested_view": view.rawValue,
            "viewpoint_verified": false,
            "target_box": masked ? targetBox : [0.0, 0.0, 1.0, 1.0],
            "quality": [
                "sharpness": quality.sharpness,
                "clipped_fraction": quality.clipped,
                "target_fraction": masked ? quality.target : 1.0,
            ],
            "quality_region": "full-frame",
            "detection_status": masked ? "suggested" : (maskPNG == nil ? "unavailable" : "skipped"),
        ]
        if masked, let maskPNG {
            selection["mask_sha256"] = sha256(maskPNG)
            capture["segmentation"] = [
                "model": "apple-vision-foreground-instance-mask",
                "revision": "iOS \(UIDevice.current.systemVersion)",
            ]
        }
        capture["selection"] = selection
        return try JSONSerialization.data(withJSONObject: capture, options: [.sortedKeys])
    }
}

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

enum PhotoProcessingError: LocalizedError {
    case unreadable, encodingFailed
    var errorDescription: String? {
        switch self {
        case .unreadable: "The photo could not be read."
        case .encodingFailed: "The photo could not be prepared for upload."
        }
    }
}

/// Turns a camera photo into an upload-ready reference photo.
enum PhotoProcessor {
    /// Longest side sent to the server (it keeps at most 2560 px).
    static let maxSide = 2048

    static func process(photoData: Data, view: CaptureAngle, profile: String, sessionID: String) throws -> CapturedPhoto {
        guard let image = UIImage(data: photoData) else { throw PhotoProcessingError.unreadable }
        // Bake orientation into the pixels and drop EXIF, so the server's
        // EXIF transpose cannot change the size the mask was made for.
        let upright = renderUpright(image, maxSide: maxSide)
        guard let cgImage = upright.cgImage, let jpeg = encodeJPEG(cgImage) else { throw PhotoProcessingError.encodingFailed }
        let quality = measure(cgImage)
        var maskPNG: Data?
        var cutout: UIImage?
        var box = [0.0, 0.0, 1.0, 1.0]
        var target = 1.0
        if let mask = try? foregroundMask(cgImage) {
            maskPNG = mask.png
            cutout = mask.cutout
            box = mask.box
            target = mask.fraction
        }
        // Only 3D objects use the cut-out by default: flat artworks are matched
        // by keypoints and scenes always use the full photo.
        let planar = profile != "multiview"
        return CapturedPhoto(
            view: view, profile: profile, jpeg: jpeg, preview: upright, maskPNG: maskPNG, cutout: cutout,
            targetBox: box, quality: (quality.sharpness, quality.clipped, target),
            sessionID: sessionID, useMask: maskPNG != nil && !planar)
    }

    static func renderUpright(_ image: UIImage, maxSide: Int) -> UIImage {
        let size = image.size
        let scale = min(1, CGFloat(maxSide) / max(size.width, size.height))
        let target = CGSize(width: floor(size.width * scale), height: floor(size.height * scale))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    static func encodeJPEG(_ image: CGImage, quality: Double = 0.9) -> Data? {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    // MARK: Quality (same definitions as the web admin's guided-quality.mjs)

    /// Variance of the 4-neighbour Laplacian and the fraction of clipped
    /// (<=8 or >=247) pixels, on a 512 px grayscale copy.
    static func measure(_ image: CGImage) -> (sharpness: Double, clipped: Double) {
        let scale = min(1, 512 / Double(max(image.width, image.height)))
        let width = max(3, Int(Double(image.width) * scale)), height = max(3, Int(Double(image.height) * scale))
        var gray = [UInt8](repeating: 0, count: width * height)
        let drawn = gray.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return (0, 1) }
        var sum = 0.0, squares = 0.0, clipped = 0
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let i = y * width + x
                let v = Double(gray[i])
                let laplacian = Double(gray[i - 1]) + Double(gray[i + 1]) + Double(gray[i - width]) + Double(gray[i + width]) - 4 * v
                sum += laplacian
                squares += laplacian * laplacian
                if gray[i] <= 8 || gray[i] >= 247 { clipped += 1 }
            }
        }
        let count = Double((width - 2) * (height - 2))
        return (max(0, squares / count - (sum / count) * (sum / count)), Double(clipped) / count)
    }

    // MARK: Foreground mask (Apple Vision, on device)

    struct Mask {
        let png: Data
        let cutout: UIImage
        let box: [Double]
        let fraction: Double
    }

    /// Binary mask (0/255, same size as the photo) of the subject nearest the
    /// centre; all subjects when none covers the centre.
    static func foregroundMask(_ image: CGImage) throws -> Mask? {
        let request = VNGenerateForegroundInstanceMaskRequest()
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        guard let observation = request.results?.first, !observation.allInstances.isEmpty else { return nil }
        let instances = centreInstance(observation).map { IndexSet(integer: $0) } ?? observation.allInstances
        let scaled = try observation.generateScaledMaskForImage(forInstances: instances, from: handler)
        let width = image.width, height = image.height
        guard CVPixelBufferGetWidth(scaled) == width, CVPixelBufferGetHeight(scaled) == height,
              CVPixelBufferLockBaseAddress(scaled, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(scaled, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(scaled) else { return nil }
        let stride = CVPixelBufferGetBytesPerRow(scaled)
        var binary = [UInt8](repeating: 0, count: width * height)
        var minX = width, minY = height, maxX = -1, maxY = -1, count = 0
        for y in 0..<height {
            let row = (base + y * stride).assumingMemoryBound(to: Float32.self)
            for x in 0..<width where row[x] >= 0.5 {
                binary[y * width + x] = 255
                count += 1
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        // Ignore specks and near-full-frame masks: neither isolates an object.
        let fraction = Double(count) / Double(width * height)
        guard count > 0, fraction >= 0.01, fraction <= 0.97 else { return nil }
        guard let png = encodeMaskPNG(binary, width: width, height: height),
              let cutout = cutout(image, mask: binary, width: width, height: height) else { return nil }
        let box = [Double(minX) / Double(width), Double(minY) / Double(height),
                   Double(maxX + 1) / Double(width), Double(maxY + 1) / Double(height)]
        return Mask(png: png, cutout: cutout, box: box, fraction: fraction)
    }

    private static func centreInstance(_ observation: VNInstanceMaskObservation) -> Int? {
        let labels = observation.instanceMask
        guard CVPixelBufferLockBaseAddress(labels, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(labels, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(labels) else { return nil }
        let width = CVPixelBufferGetWidth(labels), height = CVPixelBufferGetHeight(labels)
        let label = (base + (height / 2) * CVPixelBufferGetBytesPerRow(labels)).assumingMemoryBound(to: UInt8.self)[width / 2]
        return label == 0 ? nil : Int(label)
    }

    private static func encodeMaskPNG(_ pixels: [UInt8], width: Int, height: Int) -> Data? {
        var pixels = pixels
        guard let provider = CGDataProvider(data: Data(bytes: &pixels, count: pixels.count) as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: width,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                  provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    /// Preview only: the object on white, as recognition will see the masked photo.
    private static func cutout(_ image: CGImage, mask: [UInt8], width: Int, height: Int) -> UIImage? {
        var rgba = [UInt8](repeating: 255, count: width * height * 4)
        let ok = rgba.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard ok else { return nil }
        for i in 0..<(width * height) where mask[i] == 0 {
            rgba[i * 4] = 255; rgba[i * 4 + 1] = 255; rgba[i * 4 + 2] = 255
        }
        guard let provider = CGDataProvider(data: Data(rgba) as CFData),
              let output = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
                                   space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                                   provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
        else { return nil }
        return UIImage(cgImage: output)
    }
}
