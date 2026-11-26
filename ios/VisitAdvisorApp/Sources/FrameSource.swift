import Foundation
import VisitAdvisorKit

/// Where recognition frames come from. Every source emits the same
/// `CameraFrame` (512x512 RGB, camera-preprocessing-v1), so the recognition
/// engine, ranking and stability logic do not know which camera is in use.
@MainActor
protocol FrameSource: AnyObject {
    /// Permissions, connection and configuration. Throws a user-presentable error.
    func prepare() async throws
    /// Newest-only stream: at most one frame is waiting while one is being recognized.
    func frames() -> AsyncStream<CameraFrame>
    func start()
    func stop()
}

enum FrameSourceKind: String, CaseIterable, Identifiable, Codable {
    case phone, glasses
    var id: String { rawValue }
    var title: String {
        switch self {
        case .phone: "iPhone camera"
        case .glasses: "Ray-Ban Meta glasses"
        }
    }
}

/// Thread-safe sampling shared by sources whose frames arrive on SDK or capture
/// queues: rate limit, frame ids and the single consumer continuation.
final class FrameSampler: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: AsyncStream<CameraFrame>.Continuation?
    private var lastSample = -Double.infinity
    private var nextID = 0
    let samplesPerSecond: Double

    init(samplesPerSecond: Double = 3) { self.samplesPerSecond = samplesPerSecond }

    func makeStream() -> AsyncStream<CameraFrame> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            lock.withLock {
                self.continuation?.finish()
                self.continuation = continuation
            }
        }
    }

    /// Returns a timestamp and id when this frame should be sampled, else nil.
    /// Cheap enough to call for every incoming frame before any pixel work.
    func admit() -> (id: Int, time: Double)? {
        let now = ProcessInfo.processInfo.systemUptime * 1000
        return lock.withLock {
            guard continuation != nil, now - lastSample >= 1000 / samplesPerSecond else { return nil }
            lastSample = now
            nextID += 1
            return (nextID, now)
        }
    }

    /// Same test as `admit()` without consuming the slot.
    func wouldAdmit() -> Bool {
        let now = ProcessInfo.processInfo.systemUptime * 1000
        return lock.withLock { continuation != nil && now - lastSample >= 1000 / samplesPerSecond }
    }

    func yield(_ frame: CameraFrame) {
        lock.withLock { _ = continuation?.yield(frame) }
    }

    func finish() {
        lock.withLock {
            continuation?.finish()
            continuation = nil
            lastSample = -Double.infinity
        }
    }
}

/// Thread-safe rate limiter (e.g. preview refresh) for frames arriving off the main actor.
final class RateGate: @unchecked Sendable {
    private let lock = NSLock()
    private var last = -Double.infinity
    private let interval: Double
    init(perSecond: Double) { interval = 1000 / perSecond }
    func admit() -> Bool {
        let now = ProcessInfo.processInfo.systemUptime * 1000
        return lock.withLock {
            guard now - last >= interval else { return false }
            last = now
            return true
        }
    }
}

/// Lock-protected flag: at most one frame conversion in flight.
final class BusyFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var isSet: Bool { lock.withLock { value } }
    func trySet() -> Bool { lock.withLock { if value { return false }; value = true; return true } }
    func clear() { lock.withLock { value = false } }
}

extension CameraFrameSource: FrameSource {
    func prepare() async throws { try await configure() }
}
