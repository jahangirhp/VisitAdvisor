import Accelerate
import AVFoundation
import CoreVideo
import VisitAdvisorKit

/// One sampled, already-preprocessed camera frame (512x512 RGB, contract v1).
struct CameraFrame: Sendable {
    let id: Int
    let captureTime: Double  // milliseconds, monotonic
    let image: RGBImage
}

/// AVFoundation capture with bounded work: late frames are dropped by
/// AVFoundation, frames are sampled at `samplesPerSecond`, and the consumer
/// stream keeps only the newest pending frame (`bufferingNewest(1)`), so at most
/// one frame is being recognized and one is waiting.
final class CameraFrameSource: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    // @unchecked: session/output are configured on `sessionQueue` and frame
    // state is touched only on `captureQueue`.
    let session = AVCaptureSession()
    private let output = AVCaptureVideoDataOutput()
    private let sessionQueue = DispatchQueue(label: "camera.session")
    private let captureQueue = DispatchQueue(label: "camera.frames")
    private var continuation: AsyncStream<CameraFrame>.Continuation?
    private var lastSample = 0.0
    private var nextID = 0
    var samplesPerSecond = 3.0

    enum CameraError: LocalizedError {
        case permissionDenied, unavailable
        var errorDescription: String? {
            switch self {
            case .permissionDenied: "Camera access is off. Enable it in Settings > VisitAdvisor."
            case .unavailable: "The camera is not available."
            }
        }
    }

    static func requestPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: true
        case .notDetermined: await AVCaptureDevice.requestAccess(for: .video)
        default: false
        }
    }

    func configure() async throws {
        guard await Self.requestPermission() else { throw CameraError.permissionDenied }
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            sessionQueue.async { [self] in
                session.beginConfiguration()
                defer { session.commitConfiguration() }
                session.sessionPreset = .hd1280x720
                guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                      let input = try? AVCaptureDeviceInput(device: device),
                      session.canAddInput(input), session.canAddOutput(output)
                else { return done.resume(throwing: CameraError.unavailable) }
                session.addInput(input)
                output.alwaysDiscardsLateVideoFrames = true
                output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
                output.setSampleBufferDelegate(self, queue: captureQueue)
                session.addOutput(output)
                // Deliver upright portrait buffers so the model sees the scene as the visitor does.
                if let connection = output.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                    connection.videoRotationAngle = 90
                }
                done.resume()
            }
        }
    }

    func frames() -> AsyncStream<CameraFrame> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            captureQueue.async { self.continuation = continuation }
        }
    }

    func start() { sessionQueue.async { if !self.session.isRunning { self.session.startRunning() } } }
    func stop() { sessionQueue.async { if self.session.isRunning { self.session.stopRunning() } } }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = ProcessInfo.processInfo.systemUptime * 1000
        guard let continuation, now - lastSample >= 1000 / samplesPerSecond,
              let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer),
              let image = Self.rgbImage(from: pixelBuffer)
        else { return }
        lastSample = now
        nextID += 1
        // The 512 frame is an owned copy; the capture buffer is released here.
        continuation.yield(CameraFrame(id: nextID, captureTime: now, image: CameraPreprocessing.frame(from: image)))
    }

    /// Converts a BGRA buffer to packed RGB with vImage (honours bytesPerRow).
    static func rgbImage(from buffer: CVPixelBuffer) -> RGBImage? {
        guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_32BGRA,
              CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess
        else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }
        let width = CVPixelBufferGetWidth(buffer), height = CVPixelBufferGetHeight(buffer)
        var source = vImage_Buffer(data: base, height: vImagePixelCount(height), width: vImagePixelCount(width),
                                   rowBytes: CVPixelBufferGetBytesPerRow(buffer))
        var pixels = [UInt8](repeating: 0, count: width * height * 3)
        let status = pixels.withUnsafeMutableBytes { out -> vImage_Error in
            var destination = vImage_Buffer(data: out.baseAddress, height: vImagePixelCount(height),
                                            width: vImagePixelCount(width), rowBytes: width * 3)
            return vImageConvert_BGRA8888toRGB888(&source, &destination, vImage_Flags(kvImageNoFlags))
        }
        guard status == kvImageNoError else { return nil }
        return RGBImage(width: width, height: height, pixels: pixels)
    }
}
