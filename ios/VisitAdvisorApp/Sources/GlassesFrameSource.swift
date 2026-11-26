import CoreGraphics
import Foundation
import MWDATCamera
import MWDATCore
import UIKit
import VisitAdvisorKit

/// Ray-Ban Meta camera as a recognition frame source (Meta Wearables DAT).
///
/// Lifecycle: DeviceSession (Bluetooth LE) -> started -> camera permission
/// (granted in the Meta AI app) -> `addCamera` -> stream over the glasses'
/// Wi-Fi. Raw (decoded) 720x1280 frames at 15 fps are sampled at the same 3 Hz
/// as the phone camera and converted into the same 512 contract frame, so the
/// recognition engine is unchanged. The glasses have no viewfinder: the
/// visitor's head is the aim, and the recognized region is the centre square.
@MainActor
final class GlassesFrameSource: ObservableObject, FrameSource {
    enum GlassesError: LocalizedError {
        case notRegistered, noGlasses, sessionTimeout, permissionDenied, cameraUnavailable
        case stream(String)

        var errorDescription: String? {
            switch self {
            case .notRegistered: "Connect your glasses in VisitAdvisor first (it opens the Meta AI app)."
            case .noGlasses: "No glasses found. Put on your Ray-Ban Meta glasses and make sure they are connected in Meta AI."
            case .sessionTimeout: "The glasses did not respond. Check they are on, worn, and charged."
            case .permissionDenied: "Camera access for VisitAdvisor was not allowed in the Meta AI app."
            case .cameraUnavailable: "The glasses camera is not available right now."
            case .stream(let message): message
            }
        }
    }

    /// Latest preview image, throttled; shown full screen in place of the phone preview.
    @Published private(set) var preview: UIImage?
    @Published private(set) var streamState: StreamState = .stopped
    @Published private(set) var sessionState: DeviceSessionState = .idle

    private let link: GlassesLink
    private let configuration: StreamConfiguration
    private let sampler = FrameSampler(samplesPerSecond: 3)
    private let previewGate = RateGate(perSecond: 12)
    private var session: DeviceSession?
    private var camera: MWDATCamera.Camera?
    private let sessionTokens = ListenerTokenBag()
    private let streamTokens = ListenerTokenBag()

    init(link: GlassesLink) {
        self.link = link
        // 720x1280 raw frames at 15 fps; on a weak link the SDK lowers resolution first.
        self.configuration = StreamConfiguration(videoCodec: .raw, resolution: .high, frameRate: 15)
    }

    func prepare() async throws {
        guard link.isRegistered else { throw GlassesError.notRegistered }
        guard link.hasActiveDevice else { throw GlassesError.noGlasses }
        if session == nil { try await startSession() }
        guard let session, session.state == .started else { throw GlassesError.sessionTimeout }

        // Silent query first; requesting switches to the Meta AI app and back.
        if try await link.wearables.checkPermissionStatus(.camera) != .granted {
            guard try await link.wearables.requestPermission(.camera) == .granted else {
                throw GlassesError.permissionDenied
            }
        }
        guard camera == nil else { return }
        guard let camera = try session.addCamera(config: configuration) else { throw GlassesError.cameraUnavailable }
        self.camera = camera
        observe(camera.stream)
    }

    func frames() -> AsyncStream<CameraFrame> { sampler.makeStream() }

    func start() {
        guard let camera else { return }
        streamState = .starting
        camera.stream.start()
    }

    /// Stops the stream and ends the device session so the glasses can sleep and
    /// the phone can leave the glasses' Wi-Fi network.
    func stop() {
        sampler.finish()
        camera?.stop()
        camera = nil
        streamTokens.clear()
        session?.stop()
        session = nil
        sessionTokens.clear()
        preview = nil
        streamState = .stopped
    }

    // MARK: - Session

    private func startSession() async throws {
        let session: DeviceSession
        do {
            session = try link.wearables.createSession(deviceSelector: link.deviceSelector)
        } catch {
            if let error = error as? DeviceSessionError, error == .notRegistered { throw GlassesError.notRegistered }
            throw GlassesError.stream(error.localizedDescription)
        }
        self.session = session
        session.statePublisher.listen { [weak self] state in
            Task { @MainActor in self?.sessionChanged(state) }
        }.store(in: sessionTokens)
        session.errorPublisher.listen { [weak self] error in
            Task { @MainActor in self?.failed(error.localizedDescription) }
        }.store(in: sessionTokens)

        let states = session.stateStream()
        try session.start()
        let started = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                for await state in states {
                    if state == .started { return true }
                    if state == .stopped { return false }
                }
                return false
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(20))
                return false
            }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
        if !started {
            stop()
            throw GlassesError.sessionTimeout
        }
    }

    private func sessionChanged(_ state: DeviceSessionState) {
        sessionState = state
        if state == .stopped, session != nil {
            stop()
            onInterrupted?("The glasses disconnected.")
        }
    }

    // MARK: - Stream

    /// Called when the stream or session ends on its own (glasses removed, out of range, error).
    var onInterrupted: ((String) -> Void)?

    private func observe(_ stream: MWDATCamera.Stream) {
        stream.statePublisher.listen { [weak self] state in
            Task { @MainActor in self?.streamChanged(state) }
        }.store(in: streamTokens)
        stream.errorPublisher.listen { [weak self] error in
            Task { @MainActor in self?.failed(error.localizedDescription) }
        }.store(in: streamTokens)
        let sampler = sampler, previewGate = previewGate
        stream.videoFramePublisher.listen { [weak self] frame in
            // SDK queue. Decide cheaply before touching pixels.
            let sample = sampler.admit()
            let showPreview = previewGate.admit()
            guard sample != nil || showPreview, let image = frame.makeUIImage() else { return }
            if let sample, let rgb = Self.rgbImage(from: image) {
                sampler.yield(CameraFrame(id: sample.id, captureTime: sample.time,
                                          image: CameraPreprocessing.frame(from: rgb)))
            }
            if showPreview {
                Task { @MainActor in self?.preview = image }
            }
        }.store(in: streamTokens)
    }

    private func streamChanged(_ state: StreamState) {
        streamState = state
        // `.paused` = a cap-touch tap on the glasses; frames stop until the next tap.
        if state == .stopped, camera != nil {
            camera = nil
            streamTokens.clear()
            onInterrupted?("The glasses camera stopped.")
        }
    }

    private func failed(_ message: String) {
        onInterrupted?(message)
    }

    // MARK: - Pixels

    /// Draws a decoded frame into an sRGB RGBA bitmap and drops alpha. Orientation
    /// is applied so the model sees the scene upright, as the wearer does.
    nonisolated static func rgbImage(from image: UIImage) -> RGBImage? {
        guard let cgImage = image.cgImage else { return nil }
        let upright = image.imageOrientation == .up
        let width = upright ? cgImage.width : Int(image.size.width * image.scale)
        let height = upright ? cgImage.height : Int(image.size.height * image.scale)
        guard width > 0, height > 0 else { return nil }
        var rgba = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = rgba.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { return false }
            context.interpolationQuality = .none
            if upright {
                context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
            } else {
                UIGraphicsPushContext(context)
                context.translateBy(x: 0, y: CGFloat(height))
                context.scaleBy(x: 1, y: -1)
                image.draw(in: CGRect(x: 0, y: 0, width: width, height: height))
                UIGraphicsPopContext()
            }
            return true
        }
        guard drawn else { return nil }
        var pixels = [UInt8](repeating: 0, count: width * height * 3)
        for i in 0..<(width * height) {
            pixels[i * 3] = rgba[i * 4]
            pixels[i * 3 + 1] = rgba[i * 4 + 1]
            pixels[i * 3 + 2] = rgba[i * 4 + 2]
        }
        return RGBImage(width: width, height: height, pixels: pixels)
    }
}
