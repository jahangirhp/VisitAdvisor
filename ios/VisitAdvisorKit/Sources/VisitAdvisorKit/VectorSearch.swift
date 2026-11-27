import Accelerate
import Foundation
import Metal
import MetalPerformanceShaders

public enum VectorSearchError: Error, Equatable, Sendable {
    case dimensionMismatch
    case invalidQuery
    case gpuUnavailable
    case gpuFailed
}

/// Exact metric-defined row scores for one verified embedding space.
/// The package space is L2-normalized with metric `dot`, so a score is the raw
/// cosine `referenceMatrix × normalizedQuery` (never MongoDB's (1 + cos) / 2).
public protocol VectorSearchBackend: Sendable {
    var name: String { get }
    func scores(for query: [Float]) async throws -> [Float]
}

/// Validates and L2-normalizes a model output, matching the server's checks.
public func normalizedQuery(_ vector: [Float], dimension: Int) throws -> [Float] {
    guard vector.count == dimension, vector.allSatisfy(\.isFinite) else {
        throw VectorSearchError.invalidQuery
    }
    var norm: Float = 0
    vDSP_svesq(vector, 1, &norm, vDSP_Length(vector.count))
    norm = norm.squareRoot()
    guard norm > 0, norm.isFinite else { throw VectorSearchError.invalidQuery }
    var scale = 1 / norm
    var result = [Float](repeating: 0, count: vector.count)
    vDSP_vsmul(vector, 1, &scale, &result, 1, vDSP_Length(vector.count))
    return result
}

/// Deterministic float32 CPU baseline (Accelerate/vDSP). This is CPU work.
public struct AccelerateExactSearch: VectorSearchBackend {
    public let name = "accelerate-exact-cpu"
    private let matrix: ReferenceMatrix

    public init(matrix: ReferenceMatrix) {
        self.matrix = matrix
    }

    public func scores(for query: [Float]) async throws -> [Float] {
        try scoresSync(for: query)
    }

    public func scoresSync(for query: [Float]) throws -> [Float] {
        guard query.count == matrix.columns else { throw VectorSearchError.dimensionMismatch }
        var output = [Float](repeating: 0, count: matrix.rows)
        // (rows × columns) · (columns × 1) = rows × 1
        vDSP_mmul(
            matrix.values, 1, query, 1, &output, 1,
            vDSP_Length(matrix.rows), 1, vDSP_Length(matrix.columns)
        )
        return output
    }
}

/// GPU matrix-vector scores with Metal Performance Shaders. The reference
/// matrix is uploaded once per package into a persistent, row-padded buffer.
/// Jobs are serialized by the actor, so at most one is in flight.
public actor MetalExactSearch: VectorSearchBackend {
    public nonisolated let name = "metal-mps-exact-gpu"
    /// Padded GPU bytes, for the memory budget in diagnostics.
    public nonisolated let residentBytes: Int
    private let device: MTLDevice
    private let queue: MTLCommandQueue
    private let rows: Int
    private let columns: Int
    private let matrixBuffer: MTLBuffer
    private let matrixDescriptor: MPSMatrixDescriptor
    private let kernel: MPSMatrixVectorMultiplication
    private let queryBuffer: MTLBuffer
    private let resultBuffer: MTLBuffer

    public init(matrix: ReferenceMatrix, device: MTLDevice? = MTLCreateSystemDefaultDevice()) throws {
        guard let device, MPSSupportsMTLDevice(device), let queue = device.makeCommandQueue() else {
            throw VectorSearchError.gpuUnavailable
        }
        let rowBytes = MPSMatrixDescriptor.rowBytes(forColumns: matrix.columns, dataType: .float32)
        guard let matrixBuffer = device.makeBuffer(length: rowBytes * matrix.rows, options: .storageModeShared),
              let queryBuffer = device.makeBuffer(length: matrix.columns * 4, options: .storageModeShared),
              let resultBuffer = device.makeBuffer(length: matrix.rows * 4, options: .storageModeShared)
        else { throw VectorSearchError.gpuUnavailable }
        // Copy row by row: MPS may require padding beyond columns * 4 bytes.
        matrix.values.withUnsafeBytes { source in
            for row in 0..<matrix.rows {
                memcpy(
                    matrixBuffer.contents() + row * rowBytes,
                    source.baseAddress! + row * matrix.columns * 4,
                    matrix.columns * 4
                )
            }
        }
        self.device = device
        self.queue = queue
        self.rows = matrix.rows
        self.columns = matrix.columns
        self.matrixBuffer = matrixBuffer
        self.queryBuffer = queryBuffer
        self.resultBuffer = resultBuffer
        residentBytes = matrixBuffer.length + queryBuffer.length + resultBuffer.length
        matrixDescriptor = MPSMatrixDescriptor(
            rows: matrix.rows, columns: matrix.columns, rowBytes: rowBytes, dataType: .float32
        )
        kernel = MPSMatrixVectorMultiplication(
            device: device, transpose: false, rows: matrix.rows, columns: matrix.columns,
            alpha: 1, beta: 0
        )
    }


    public func scores(for query: [Float]) async throws -> [Float] {
        guard query.count == columns else { throw VectorSearchError.dimensionMismatch }
        query.withUnsafeBytes { memcpy(queryBuffer.contents(), $0.baseAddress!, columns * 4) }
        guard let commands = queue.makeCommandBuffer() else { throw VectorSearchError.gpuFailed }
        let matrix = MPSMatrix(buffer: matrixBuffer, descriptor: matrixDescriptor)
        let input = MPSVector(
            buffer: queryBuffer,
            descriptor: MPSVectorDescriptor(length: columns, dataType: .float32)
        )
        let output = MPSVector(
            buffer: resultBuffer,
            descriptor: MPSVectorDescriptor(length: rows, dataType: .float32)
        )
        kernel.encode(commandBuffer: commands, inputMatrix: matrix, inputVector: input, resultVector: output)
        // Wait for completion without blocking a thread; buffers stay alive
        // because the actor owns them and serializes jobs.
        await withCheckedContinuation { continuation in
            commands.addCompletedHandler { _ in continuation.resume() }
            commands.commit()
        }
        guard commands.status == .completed else { throw VectorSearchError.gpuFailed }
        let pointer = resultBuffer.contents().bindMemory(to: Float.self, capacity: rows)
        return Array(UnsafeBufferPointer(start: pointer, count: rows))
    }
}
