import Foundation
import MWDATCore
import SwiftUI

#if DEBUG
import MWDATMockDevice
#endif

/// App-wide link to Ray-Ban Meta glasses through the Meta Wearables Device
/// Access Toolkit: registration with the Meta AI app, and whether a pair of
/// glasses is currently connected and worn. Streaming lives in
/// `GlassesFrameSource`; this object only answers "can we stream right now?".
@MainActor
final class GlassesLink: ObservableObject {
    @Published private(set) var registration: RegistrationState
    @Published private(set) var hasActiveDevice = false
    @Published private(set) var deviceCount = 0
    @Published var error: String?
    #if DEBUG
    @Published private(set) var mockEnabled = false
    #endif

    let wearables: WearablesInterface
    let deviceSelector: AutoDeviceSelector
    private var tasks: [Task<Void, Never>] = []

    /// Call `Wearables.configure()` before creating this (see `VisitAdvisorApp.init`).
    init(wearables: WearablesInterface = Wearables.shared) {
        self.wearables = wearables
        self.deviceSelector = AutoDeviceSelector(wearables: wearables)
        self.registration = wearables.registrationState
        tasks.append(Task { [weak self] in
            for await state in wearables.registrationStateStream() { self?.registration = state }
        })
        tasks.append(Task { [weak self] in
            for await devices in wearables.devicesStream() { self?.deviceCount = devices.count }
        })
        tasks.append(Task { [weak self, deviceSelector] in
            for await device in deviceSelector.activeDeviceStream() { self?.hasActiveDevice = device != nil }
        })
    }

    var isRegistered: Bool { registration == .registered }

    var statusText: String {
        switch registration {
        case .registered: hasActiveDevice ? "Glasses connected" : "Registered — put on your glasses"
        case .registering: "Connecting in Meta AI…"
        case .available: "Not connected"
        case .unavailable: "Meta AI app not available"
        }
    }

    /// Opens the Meta AI app to approve VisitAdvisor; it returns via `handle(url:)`.
    func connect() {
        guard registration != .registering else { return }
        Task {
            do { try await wearables.startRegistration() } catch { self.error = error.localizedDescription }
        }
    }

    func disconnect() {
        Task {
            do { try await wearables.startUnregistration() } catch { self.error = error.localizedDescription }
        }
    }

    /// Handles the Meta AI callback (`visitadvisor://?metaWearablesAction=…`).
    /// Returns false for URLs that are not DAT callbacks.
    @discardableResult
    func handle(url: URL) -> Bool {
        guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              items.contains(where: { $0.name == "metaWearablesAction" })
        else { return false }
        Task {
            do { _ = try await wearables.handleUrl(url) } catch { self.error = error.localizedDescription }
        }
        return true
    }

    #if DEBUG
    /// Simulated Ray-Ban Meta glasses (registered, permissions granted, worn).
    /// Their camera shows the phone's back camera, or a video file when one is
    /// set with `useMockVideo(_:)` — useful for replaying recorded museum walks.
    private var mockGlasses: (any MockGlasses)?

    func enableMockGlasses() {
        guard mockGlasses == nil else { return }
        do {
            MockDeviceKit.shared.enable()
            let glasses = try MockDeviceKit.shared.pairGlasses(model: .rayBanMeta)
            glasses.powerOn()
            glasses.unfold()
            glasses.don()
            glasses.services.camera.setCameraFeed(cameraFacing: .back)
            mockGlasses = glasses
            mockEnabled = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    func useMockVideo(_ url: URL) {
        mockGlasses?.services.camera.setCameraFeed(fileURL: url)
    }

    func disableMockGlasses() {
        if let mockGlasses { MockDeviceKit.shared.unpairDevice(mockGlasses) }
        MockDeviceKit.shared.disable()
        mockGlasses = nil
        mockEnabled = false
    }
    #endif
}
