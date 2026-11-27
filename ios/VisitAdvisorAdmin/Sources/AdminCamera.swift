import AVFoundation
import CoreImage
import Security
import SwiftUI
import UIKit

/// Stores the admin token in the Keychain (this device only, never backed up).
enum TokenStore {
    private static let service = "com.steeltrace.visitadvisor.admin"
    private static let account = "admin-token"

    static func load() -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String) {
        clear()
        let item: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                   kSecAttrAccount as String: account, kSecValueData as String: Data(token.utf8),
                                   kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        SecItemAdd(item as CFDictionary, nil)
    }

    static func clear() {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                       kSecAttrAccount as String: account] as CFDictionary)
    }
}

/// Back camera with a full-resolution photo output (reference capture) and a
/// sampled video output (live score test). Portrait, upright buffers.
final class AdminCamera: NSObject, @unchecked Sendable, AVCapturePhotoCaptureDelegate, AVCaptureVideoDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let queue = DispatchQueue(label: "admin.camera")
    private let frameQueue = DispatchQueue(label: "admin.camera.frames")
    private var photoContinuation: CheckedContinuation<Data, Error>?
    private let lock = NSLock()
    private var frameHandler: ((CVPixelBuffer) -> Void)?

    enum CameraError: LocalizedError {
        case denied, unavailable, captureFailed
        var errorDescription: String? {
            switch self {
            case .denied: "Camera access is off. Enable it in Settings."
            case .unavailable: "The camera is not available."
            case .captureFailed: "The photo could not be taken. Try again."
            }
        }
    }

    func start(withVideoFrames: Bool) async throws {
        let allowed: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: allowed = true
        case .notDetermined: allowed = await AVCaptureDevice.requestAccess(for: .video)
        default: allowed = false
        }
        guard allowed else { throw CameraError.denied }
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                if session.inputs.isEmpty {
                    session.beginConfiguration()
                    session.sessionPreset = .photo
                    guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                          let input = try? AVCaptureDeviceInput(device: device),
                          session.canAddInput(input), session.canAddOutput(photoOutput), session.canAddOutput(videoOutput)
                    else {
                        session.commitConfiguration()
                        return done.resume(throwing: CameraError.unavailable)
                    }
                    session.addInput(input)
                    session.addOutput(photoOutput)
                    videoOutput.alwaysDiscardsLateVideoFrames = true
                    videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
                    videoOutput.setSampleBufferDelegate(self, queue: frameQueue)
                    session.addOutput(videoOutput)
                    for output in [photoOutput as AVCaptureOutput, videoOutput] {
                        if let connection = output.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                            connection.videoRotationAngle = 90
                        }
                    }
                    session.commitConfiguration()
                }
                if !session.isRunning { session.startRunning() }
                done.resume()
            }
        }
    }

    func stop() {
        setFrameHandler(nil)
        queue.async { if self.session.isRunning { self.session.stopRunning() } }
    }

    func setFrameHandler(_ handler: ((CVPixelBuffer) -> Void)?) {
        lock.withLock { frameHandler = handler }
    }

    func capturePhoto() async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                photoContinuation = continuation
                let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
                photoOutput.capturePhoto(with: settings, delegate: self)
            }
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        queue.async { [self] in
            let continuation = photoContinuation
            photoContinuation = nil
            if let data = photo.fileDataRepresentation(), error == nil {
                continuation?.resume(returning: data)
            } else {
                continuation?.resume(throwing: error ?? CameraError.captureFailed)
            }
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let handler = lock.withLock({ frameHandler }), let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        handler(buffer)
    }
}

struct AdminCameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    var fill = true

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = fill ? .resizeAspectFill : .resizeAspect
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}
}

/// The website's frame: centre square of 224/256 of the short side, resized to
/// `size` (224 for the standard pipeline), JPEG — what `/live/recognize` expects.
enum LiveFrame {
    private static let context = CIContext()

    static func jpeg(from buffer: CVPixelBuffer, size: Int) -> Data? {
        let image = CIImage(cvPixelBuffer: buffer)
        let extent = image.extent
        let side = min(extent.width, extent.height) * 224 / 256
        let crop = CGRect(x: extent.midX - side / 2, y: extent.midY - side / 2, width: side, height: side)
        let scale = CGFloat(size) / side
        let output = image.cropped(to: crop)
            .transformed(by: CGAffineTransform(translationX: -crop.minX, y: -crop.minY))
            .transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let cgImage = context.createCGImage(output, from: CGRect(x: 0, y: 0, width: size, height: size)) else { return nil }
        return PhotoProcessor.encodeJPEG(cgImage, quality: 0.92)
    }
}
