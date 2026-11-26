// Narrow Objective-C++ boundary to OpenCV. Swift sees only Foundation types;
// cv::Mat never crosses this header. All C++ exceptions are caught inside.
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// SIFT keypoints/descriptors for one image (or one package reference view).
@interface VALocalFeatures : NSObject
/// float32 x,y pairs, count * 2 values.
@property (nonatomic, readonly) NSData *points;
/// float32 descriptors, count * 128 values.
@property (nonatomic, readonly) NSData *descriptors;
@property (nonatomic, readonly) NSInteger count;
@property (nonatomic, readonly) double width;
@property (nonatomic, readonly) double height;
- (instancetype)initWithPoints:(NSData *)points
                   descriptors:(NSData *)descriptors
                         count:(NSInteger)count
                         width:(double)width
                        height:(double)height NS_DESIGNATED_INITIALIZER;
- (instancetype)init NS_UNAVAILABLE;
@end

@interface VAGeometricEvidence : NSObject
@property (nonatomic, readonly) double score;
@property (nonatomic, readonly) double queryCoverage;
@property (nonatomic, readonly) double referenceCoverage;
@end

@interface VAOpenCVBridge : NSObject
/// `regions.proposals` on a 224x224 RGB8 image (already Pillow-bilinear
/// resized by Swift). Returns up to `maximum` (≤ 2) boxes as
/// [x0, y0, x1, y1] normalized doubles, server order.
+ (nullable NSArray<NSArray<NSNumber *> *> *)proposalsForRGB224:(NSData *)rgb
                                                        maximum:(NSInteger)maximum
                                                          error:(NSError **)error;

/// `geometry.describe_local`: grayscale SIFT, nfeatures 800.
+ (nullable VALocalFeatures *)describeRGB:(NSData *)rgb
                                    width:(NSInteger)width
                                   height:(NSInteger)height
                                    error:(NSError **)error;

/// `features.geometric_evidence`: ratio test, unique reference points,
/// RANSAC homography (seed 0), inlier score and convex-hull coverage.
+ (nullable VAGeometricEvidence *)evidenceForQuery:(VALocalFeatures *)query
                                         reference:(VALocalFeatures *)reference
                                             error:(NSError **)error;
@end

NS_ASSUME_NONNULL_END
