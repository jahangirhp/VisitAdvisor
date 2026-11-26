// OpenCV must be imported before Apple headers (its NO/YES macros clash).
#ifdef __cplusplus
#import <opencv2/core.hpp>
#import <opencv2/imgproc.hpp>
#import <opencv2/features2d.hpp>
#import <opencv2/calib3d.hpp>
#endif

#import "VAOpenCVBridge.h"

#include <algorithm>
#include <cmath>
#include <unordered_map>
#include <vector>

static NSString *const VAOpenCVErrorDomain = @"VAOpenCVBridge";

static void fail(NSError **error, NSString *code) {
    if (error) *error = [NSError errorWithDomain:VAOpenCVErrorDomain code:1 userInfo:@{NSLocalizedDescriptionKey: code}];
}

// Python's round(): half to even.
static int pyRound(double value) { return (int)std::nearbyint(value); }

@implementation VALocalFeatures
- (instancetype)initWithPoints:(NSData *)points descriptors:(NSData *)descriptors count:(NSInteger)count
                         width:(double)width height:(double)height {
    if ((self = [super init])) {
        _points = [points copy];
        _descriptors = [descriptors copy];
        _count = count;
        _width = width;
        _height = height;
    }
    return self;
}
@end

@interface VAGeometricEvidence ()
- (instancetype)initWithScore:(double)score query:(double)query reference:(double)reference;
@end

@implementation VAGeometricEvidence
- (instancetype)initWithScore:(double)score query:(double)query reference:(double)reference {
    if ((self = [super init])) {
        _score = score;
        _queryCoverage = query;
        _referenceCoverage = reference;
    }
    return self;
}
@end

static double overlap(const cv::Vec4d &a, const cv::Vec4d &b) {
    double intersection = std::max(0.0, std::min(a[2], b[2]) - std::max(a[0], b[0])) *
                          std::max(0.0, std::min(a[3], b[3]) - std::max(a[1], b[1]));
    if (intersection == 0) return 0;
    double areaA = (a[2] - a[0]) * (a[3] - a[1]);
    double areaB = (b[2] - b[0]) * (b[3] - b[1]);
    return intersection / (areaA + areaB - intersection);
}

// numpy.percentile default (linear interpolation).
static double percentile(std::vector<float> values, double q) {
    std::sort(values.begin(), values.end());
    double position = q / 100.0 * (values.size() - 1);
    size_t lower = (size_t)std::floor(position);
    size_t upper = std::min(lower + 1, values.size() - 1);
    double fraction = position - lower;
    return values[lower] + (values[upper] - values[lower]) * fraction;
}

static float median(std::vector<float> values) {
    std::sort(values.begin(), values.end());
    size_t n = values.size();
    return n % 2 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2.0f;
}

@implementation VAOpenCVBridge

+ (NSArray<NSArray<NSNumber *> *> *)proposalsForRGB224:(NSData *)rgbData maximum:(NSInteger)maximum error:(NSError **)error {
    if (maximum < 0 || maximum > 2 || rgbData.length != 224 * 224 * 3) { fail(error, @"INVALID_PROPOSAL_INPUT"); return nil; }
    if (maximum == 0) return @[];
    try {
        cv::setNumThreads(1);
        cv::Mat rgb(224, 224, CV_8UC3, (void *)rgbData.bytes);  // view; rgbData outlives it
        cv::Mat gray, edges;
        cv::cvtColor(rgb, gray, cv::COLOR_RGB2GRAY);
        cv::GaussianBlur(gray, gray, cv::Size(3, 3), 0);
        cv::Canny(gray, edges, 40, 120);
        cv::morphologyEx(edges, edges, cv::MORPH_CLOSE, cv::Mat::ones(3, 3, CV_8U));

        cv::Mat lab8, lab;
        cv::cvtColor(rgb, lab8, cv::COLOR_RGB2Lab);
        lab8.convertTo(lab, CV_32FC3);
        // Border rows/columns in numpy order: top, bottom, left, right.
        float background[3];
        for (int c = 0; c < 3; c++) {
            std::vector<float> border;
            border.reserve(224 * 4);
            for (int x = 0; x < 224; x++) border.push_back(lab.at<cv::Vec3f>(0, x)[c]);
            for (int x = 0; x < 224; x++) border.push_back(lab.at<cv::Vec3f>(223, x)[c]);
            for (int y = 0; y < 224; y++) border.push_back(lab.at<cv::Vec3f>(y, 0)[c]);
            for (int y = 0; y < 224; y++) border.push_back(lab.at<cv::Vec3f>(y, 223)[c]);
            background[c] = median(border);
        }
        std::vector<float> contrast(224 * 224);
        for (int y = 0; y < 224; y++) {
            for (int x = 0; x < 224; x++) {
                cv::Vec3f v = lab.at<cv::Vec3f>(y, x);
                float d0 = v[0] - background[0], d1 = v[1] - background[1], d2 = v[2] - background[2];
                contrast[y * 224 + x] = std::sqrt(d0 * d0 + d1 * d1 + d2 * d2);
            }
        }
        double threshold = std::max(20.0, percentile(contrast, 55));
        cv::Mat foreground(224, 224, CV_8U);
        for (int i = 0; i < 224 * 224; i++) foreground.data[i] = contrast[i] > threshold ? 255 : 0;
        cv::morphologyEx(foreground, foreground, cv::MORPH_CLOSE, cv::Mat::ones(5, 5, CV_8U));

        struct Candidate { double score; cv::Vec4d box; };
        std::vector<Candidate> candidates;
        for (cv::Mat *mask : {&edges, &foreground}) {
            std::vector<std::vector<cv::Point>> contours;
            cv::findContours(*mask, contours, cv::RETR_LIST, cv::CHAIN_APPROX_SIMPLE);
            std::vector<std::pair<double, size_t>> ordered;
            for (size_t i = 0; i < contours.size(); i++) ordered.push_back({cv::contourArea(contours[i]), i});
            std::stable_sort(ordered.begin(), ordered.end(), [](auto &a, auto &b) { return a.first > b.first; });
            if (ordered.size() > 100) ordered.resize(100);
            for (auto &[contourArea, index] : ordered) {
                cv::Rect r = cv::boundingRect(contours[index]);
                double area = (double)r.width * r.height / (224.0 * 224.0);
                if (!(0.08 <= area && area <= 0.85) || std::min(r.width, r.height) < 28) continue;
                double fill = contourArea / ((double)r.width * r.height);
                if (fill < 0.25) continue;
                int padding = std::max(3, pyRound(std::min(r.width, r.height) * 0.06));
                cv::Vec4d box(std::max(0, r.x - padding) / 224.0, std::max(0, r.y - padding) / 224.0,
                              std::min(224, r.x + r.width + padding) / 224.0,
                              std::min(224, r.y + r.height + padding) / 224.0);
                double dx = (r.x + r.width / 2.0) / 224.0 - 0.5, dy = (r.y + r.height / 2.0) / 224.0 - 0.5;
                candidates.push_back({fill * area / (1 + 4 * (dx * dx + dy * dy)), box});
            }
        }
        // Python sorts (score, box) tuples descending.
        std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate &a, const Candidate &b) {
            if (a.score != b.score) return a.score > b.score;
            for (int i = 0; i < 4; i++) if (a.box[i] != b.box[i]) return a.box[i] > b.box[i];
            return false;
        });
        const cv::Vec4d full(0, 0, 1, 1);
        NSMutableArray *selected = [NSMutableArray array];
        std::vector<cv::Vec4d> chosen;
        for (auto &candidate : candidates) {
            if (overlap(candidate.box, full) > 0.9) continue;
            bool duplicate = false;
            for (auto &previous : chosen) duplicate = duplicate || overlap(candidate.box, previous) > 0.7;
            if (duplicate) continue;
            chosen.push_back(candidate.box);
            [selected addObject:@[@(candidate.box[0]), @(candidate.box[1]), @(candidate.box[2]), @(candidate.box[3])]];
            if ((NSInteger)chosen.size() == maximum) break;
        }
        return selected;
    } catch (...) {
        fail(error, @"PROPOSALS_FAILED");
        return nil;
    }
}

+ (VALocalFeatures *)describeRGB:(NSData *)rgbData width:(NSInteger)width height:(NSInteger)height error:(NSError **)error {
    if (width <= 0 || height <= 0 || rgbData.length != (NSUInteger)(width * height * 3)) { fail(error, @"INVALID_IMAGE"); return nil; }
    try {
        cv::setNumThreads(1);
        cv::Mat rgb((int)height, (int)width, CV_8UC3, (void *)rgbData.bytes);
        cv::Mat gray;
        cv::cvtColor(rgb, gray, cv::COLOR_RGB2GRAY);
        std::vector<cv::KeyPoint> keypoints;
        cv::Mat descriptors;
        cv::SIFT::create(800)->detectAndCompute(gray, cv::noArray(), keypoints, descriptors);
        size_t count = std::min<size_t>(800, keypoints.size());
        if (descriptors.empty()) count = 0;
        std::vector<float> points(count * 2);
        for (size_t i = 0; i < count; i++) { points[i * 2] = keypoints[i].pt.x; points[i * 2 + 1] = keypoints[i].pt.y; }
        NSData *descriptorData = count
            ? [NSData dataWithBytes:descriptors.rowRange(0, (int)count).clone().data length:count * 128 * sizeof(float)]
            : [NSData data];
        return [[VALocalFeatures alloc] initWithPoints:[NSData dataWithBytes:points.data() length:points.size() * sizeof(float)]
                                           descriptors:descriptorData count:(NSInteger)count width:width height:height];
    } catch (...) {
        fail(error, @"DESCRIBE_FAILED");
        return nil;
    }
}

static cv::Mat descriptorMat(VALocalFeatures *f) {
    return cv::Mat((int)f.count, 128, CV_32F, (void *)f.descriptors.bytes);
}

+ (VAGeometricEvidence *)evidenceForQuery:(VALocalFeatures *)query reference:(VALocalFeatures *)reference error:(NSError **)error {
    VAGeometricEvidence *empty = [[VAGeometricEvidence alloc] initWithScore:0 query:0 reference:0];
    if (query.points.length != (NSUInteger)query.count * 8 || query.descriptors.length != (NSUInteger)query.count * 512 ||
        reference.points.length != (NSUInteger)reference.count * 8 || reference.descriptors.length != (NSUInteger)reference.count * 512) {
        fail(error, @"INVALID_FEATURES");
        return nil;
    }
    if (std::min(query.count, reference.count) < 8) return empty;
    try {
        std::vector<std::vector<cv::DMatch>> matches;
        cv::BFMatcher().knnMatch(descriptorMat(query), descriptorMat(reference), matches, 2);
        std::vector<cv::DMatch> good;
        for (auto &pair : matches)
            if (pair.size() == 2 && pair[0].distance < 0.7f * pair[1].distance) good.push_back(pair[0]);
        // {m.trainIdx: m for m in sorted(good, key=-distance)}: last (closest)
        // value wins, but each key keeps its first insertion position.
        std::stable_sort(good.begin(), good.end(), [](const cv::DMatch &a, const cv::DMatch &b) { return a.distance > b.distance; });
        std::vector<int> order;
        std::unordered_map<int, cv::DMatch> byTrain;
        for (auto &m : good) {
            if (!byTrain.count(m.trainIdx)) order.push_back(m.trainIdx);
            byTrain[m.trainIdx] = m;
        }
        if (order.size() < 8) return empty;
        const float *qp = (const float *)query.points.bytes, *rp = (const float *)reference.points.bytes;
        std::vector<cv::Point2f> src, dst;
        for (int train : order) {
            const cv::DMatch &m = byTrain[train];
            src.emplace_back(qp[m.queryIdx * 2], qp[m.queryIdx * 2 + 1]);
            dst.emplace_back(rp[m.trainIdx * 2], rp[m.trainIdx * 2 + 1]);
        }
        cv::setRNGSeed(0);
        cv::Mat mask;
        cv::Mat matrix = cv::findHomography(src, dst, cv::RANSAC, 4.0, mask);
        if (matrix.empty() || mask.empty() || !cv::checkRange(matrix)) return empty;
        int inliers = cv::countNonZero(mask);
        if (inliers < 8) return empty;
        auto coverage = [&](const std::vector<cv::Point2f> &points, double w, double h) {
            std::vector<cv::Point2f> selected, hull;
            for (size_t i = 0; i < points.size(); i++) if (mask.at<uchar>((int)i)) selected.push_back(points[i]);
            cv::convexHull(selected, hull);
            return std::min(1.0, std::max(0.0, cv::contourArea(hull) / (w * h)));
        };
        double score = (double)inliers / order.size() * std::min(inliers / 20.0, 1.0);
        return [[VAGeometricEvidence alloc] initWithScore:score
                                                    query:coverage(src, query.width, query.height)
                                                reference:coverage(dst, reference.width, reference.height)];
    } catch (...) {
        fail(error, @"GEOMETRY_FAILED");
        return nil;
    }
}

@end
