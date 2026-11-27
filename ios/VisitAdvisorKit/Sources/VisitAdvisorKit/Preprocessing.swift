import Foundation

/// Interleaved 8-bit RGB image, row-major, origin top-left.
public struct RGBImage: Equatable, Sendable {
    public let width: Int
    public let height: Int
    public var pixels: [UInt8]

    public init(width: Int, height: Int, pixels: [UInt8]) {
        precondition(width > 0 && height > 0 && pixels.count == width * height * 3)
        self.width = width
        self.height = height
        self.pixels = pixels
    }
}

/// Bit-exact port of Pillow's 8-bit separable resampler (`Resample.c`):
/// same filter supports, coefficient normalization, 22-bit fixed point,
/// horizontal-then-vertical passes and clamping. Verified against Pillow in
/// `scripts/ios_golden_fixtures.py`; the server contract is defined in Pillow.
public enum PillowResample {
    public enum Filter: Sendable {
        case bilinear, bicubic

        var support: Double { self == .bilinear ? 1 : 2 }

        func weight(_ value: Double) -> Double {
            let x = abs(value)
            switch self {
            case .bilinear:
                return x < 1 ? 1 - x : 0
            case .bicubic:
                let a = -0.5
                if x < 1 { return ((a + 2) * x - (a + 3)) * x * x + 1 }
                if x < 2 { return (((x - 5) * x + 8) * x - 4) * a }
                return 0
            }
        }
    }

    struct Coefficients {
        let bounds: [(start: Int, count: Int)]
        let weights: [[Int64]]
    }

    static let precisionBits = 22

    static func coefficients(inSize: Int, in0: Double, in1: Double, outSize: Int, filter: Filter) -> Coefficients {
        let scale = (in1 - in0) / Double(outSize)
        let filterScale = max(scale, 1)
        let support = filter.support * filterScale
        var bounds: [(Int, Int)] = []
        var weights: [[Int64]] = []
        bounds.reserveCapacity(outSize)
        for xx in 0..<outSize {
            let center = in0 + (Double(xx) + 0.5) * scale
            let inverse = 1 / filterScale
            let xmin = max(Int(center - support + 0.5), 0)  // C cast truncates
            let xmax = min(Int(center + support + 0.5), inSize) - xmin
            var k = (0..<max(xmax, 0)).map { filter.weight((Double($0 + xmin) - center + 0.5) * inverse) }
            let total = k.reduce(0, +)
            if total != 0 { k = k.map { $0 / total } }
            weights.append(k.map { Int64($0 * Double(1 << precisionBits) + ($0 < 0 ? -0.5 : 0.5)) })
            bounds.append((xmin, max(xmax, 0)))
        }
        return Coefficients(bounds: bounds, weights: weights)
    }

    @inline(__always) static func clip8(_ value: Int64) -> UInt8 {
        let shifted = value >> Int64(precisionBits)
        return shifted < 0 ? 0 : shifted > 255 ? 255 : UInt8(shifted)
    }

    /// `Image.resize((width, height), filter, box=box)` for an RGB image.
    public static func resize(
        _ image: RGBImage, width: Int, height: Int,
        box: (x0: Double, y0: Double, x1: Double, y1: Double)? = nil, filter: Filter
    ) -> RGBImage {
        let box = box ?? (0, 0, Double(image.width), Double(image.height))
        let needHorizontal = width != image.width || box.x0 != 0 || box.x1 != Double(image.width)
        let needVertical = height != image.height || box.y0 != 0 || box.y1 != Double(image.height)
        let horizontal = coefficients(inSize: image.width, in0: box.x0, in1: box.x1, outSize: width, filter: filter)
        var vertical = coefficients(inSize: image.height, in0: box.y0, in1: box.y1, outSize: height, filter: filter)
        let yFirst = vertical.bounds[0].start
        let yLast = vertical.bounds[height - 1].start + vertical.bounds[height - 1].count
        let half: Int64 = 1 << Int64(precisionBits - 1)

        var source = image.pixels
        var sourceWidth = image.width
        var sourceHeight = image.height
        if needHorizontal {
            vertical = Coefficients(
                bounds: vertical.bounds.map { ($0.start - yFirst, $0.count) }, weights: vertical.weights
            )
            let rows = yLast - yFirst
            var output = [UInt8](repeating: 0, count: rows * width * 3)
            image.pixels.withUnsafeBufferPointer { input in
                for y in 0..<rows {
                    let inRow = (y + yFirst) * image.width * 3
                    for x in 0..<width {
                        let (start, count) = horizontal.bounds[x]
                        let k = horizontal.weights[x]
                        var r = half, g = half, b = half
                        for i in 0..<count {
                            let p = inRow + (start + i) * 3
                            r += Int64(input[p]) * k[i]
                            g += Int64(input[p + 1]) * k[i]
                            b += Int64(input[p + 2]) * k[i]
                        }
                        let o = (y * width + x) * 3
                        output[o] = clip8(r); output[o + 1] = clip8(g); output[o + 2] = clip8(b)
                    }
                }
            }
            source = output
            sourceWidth = width
            sourceHeight = rows
        }
        if needVertical {
            var output = [UInt8](repeating: 0, count: height * sourceWidth * 3)
            source.withUnsafeBufferPointer { input in
                for y in 0..<height {
                    let (start, count) = vertical.bounds[y]
                    let k = vertical.weights[y]
                    for x in 0..<sourceWidth {
                        var r = half, g = half, b = half
                        for i in 0..<count {
                            let p = ((start + i) * sourceWidth + x) * 3
                            r += Int64(input[p]) * k[i]
                            g += Int64(input[p + 1]) * k[i]
                            b += Int64(input[p + 2]) * k[i]
                        }
                        let o = (y * sourceWidth + x) * 3
                        output[o] = clip8(r); output[o + 1] = clip8(g); output[o + 2] = clip8(b)
                    }
                }
            }
            source = output
            sourceHeight = height
        }
        return RGBImage(width: sourceWidth, height: sourceHeight, pixels: source)
    }

    /// `Image.crop` with integer bounds (inside the image).
    public static func crop(_ image: RGBImage, x0: Int, y0: Int, x1: Int, y1: Int) -> RGBImage {
        precondition(0 <= x0 && x0 < x1 && x1 <= image.width && 0 <= y0 && y0 < y1 && y1 <= image.height)
        var pixels = [UInt8]()
        pixels.reserveCapacity((x1 - x0) * (y1 - y0) * 3)
        for y in y0..<y1 {
            let row = (y * image.width + x0) * 3
            pixels.append(contentsOf: image.pixels[row..<(row + (x1 - x0) * 3)])
        }
        return RGBImage(width: x1 - x0, height: y1 - y0, pixels: pixels)
    }
}

/// The server camera contract (`camera-preprocessing-v1`, pipeline v5).
public enum CameraPreprocessing {
    public static let frameSize = 512
    public static let modelSize = 224
    static let mean: [Float] = [0.485, 0.456, 0.406]
    static let std: [Float] = [0.229, 0.224, 0.225]

    /// Center square of side min(w,h)·224/256, resampled to the 512 px frame.
    /// The browser path also JPEG-encodes here; the phone does not (documented
    /// difference, measured in model parity rather than assumed equal).
    public static func frame(from image: RGBImage, size: Int = frameSize) -> RGBImage {
        let side = Double(min(image.width, image.height)) * 224 / 256
        let x0 = (Double(image.width) - side) / 2
        let y0 = (Double(image.height) - side) / 2
        return PillowResample.resize(
            image, width: size, height: size, box: (x0, y0, x0 + side, y0 + side), filter: .bicubic
        )
    }

    /// `max(ImageStat.Stat(image).stddev) < 2` → unreadable frame.
    public static func isUnreadable(_ image: RGBImage) -> Bool {
        let count = Double(image.width * image.height)
        var sum = [Double](repeating: 0, count: 3)
        var squares = [Double](repeating: 0, count: 3)
        for i in stride(from: 0, to: image.pixels.count, by: 3) {
            for c in 0..<3 {
                let v = Double(image.pixels[i + c])
                sum[c] += v
                squares[c] += v * v
            }
        }
        let deviations = (0..<3).map { c -> Double in
            let mean = sum[c] / count
            return max(squares[c] / count - mean * mean, 0).squareRoot()
        }
        return deviations.max()! < 2
    }

    /// `regions.crop`: Python `round()` (half to even) box, resized back to frame size.
    public static func crop(_ image: RGBImage, box: NormalizedBox) -> RGBImage {
        func r(_ value: Float, _ size: Int) -> Int {
            Int((Double(value) * Double(size)).rounded(.toNearestOrEven))
        }
        let cropped = PillowResample.crop(
            image, x0: r(box.x0, image.width), y0: r(box.y0, image.height),
            x1: r(box.x1, image.width), y1: r(box.y1, image.height)
        )
        return PillowResample.resize(cropped, width: image.width, height: image.height, filter: .bicubic)
    }

    /// `pipeline.embedding` input: 224 bicubic, /255, ImageNet normalization, NCHW.
    public static func modelInput(_ region: RGBImage) -> [Float] {
        let resized = PillowResample.resize(region, width: modelSize, height: modelSize, filter: .bicubic)
        let plane = modelSize * modelSize
        var tensor = [Float](repeating: 0, count: 3 * plane)
        for i in 0..<plane {
            for c in 0..<3 {
                tensor[c * plane + i] = (Float(resized.pixels[i * 3 + c]) / 255 - mean[c]) / std[c]
            }
        }
        return tensor
    }
}
