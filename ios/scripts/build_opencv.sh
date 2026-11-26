#!/usr/bin/env bash
# Build a minimal static OpenCV for the app as Vendor/opencv2.xcframework
# (iPhone arm64 + Apple-silicon simulator arm64).
#
# Uses plain CMake + Makefiles instead of OpenCV's Xcode-based framework script,
# which fails with newer Xcode versions. Only the modules the pipeline uses are
# built: core, imgproc, features2d (SIFT), calib3d (findHomography), flann.
#
# Pinned to OpenCV 4.14.0 (the server's opencv-python-headless). ARM-only
# acceleration (Carotene, KleidiCV) is OFF so results stay closer to the x86
# server build; parity is still measured, not assumed.
#
# Requires Xcode and CMake (brew install cmake). Takes ~10-20 minutes.
set -euo pipefail
VERSION=4.14.0
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$HERE/.build/opencv-cmake"
SRC="$WORK/opencv-$VERSION"
mkdir -p "$WORK" "$HERE/Vendor"
if [ ! -d "$SRC" ]; then
  curl -fL "https://github.com/opencv/opencv/archive/refs/tags/$VERSION.tar.gz" | tar -xz -C "$WORK"
fi
JOBS="$(sysctl -n hw.ncpu)"

build_sdk() {
  local sdk="$1"
  local build="$WORK/build-$sdk" install="$WORK/install-$sdk"
  rm -rf "$build" "$install"
  cmake -S "$SRC" -B "$build" -G "Unix Makefiles" \
    -DCMAKE_SYSTEM_NAME=iOS \
    -DCMAKE_OSX_SYSROOT="$sdk" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 \
    -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$install" \
    -DBUILD_LIST=core,imgproc,features2d,calib3d,flann \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_opencv_apps=OFF -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF -DBUILD_DOCS=OFF -DBUILD_JAVA=OFF -DBUILD_opencv_python3=OFF \
    -DBUILD_ZLIB=ON \
    -DWITH_CAROTENE=OFF -DWITH_KLEIDICV=OFF -DWITH_IPP=OFF -DWITH_ITT=OFF -DWITH_OPENCL=OFF \
    -DWITH_PROTOBUF=OFF -DWITH_ADE=OFF -DWITH_FLATBUFFERS=OFF -DWITH_EIGEN=OFF -DWITH_LAPACK=OFF \
    -DWITH_JPEG=OFF -DWITH_PNG=OFF -DWITH_WEBP=OFF -DWITH_AVIF=OFF -DWITH_TIFF=OFF \
    -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_OPENEXR=OFF -DWITH_IMGCODEC_HDR=OFF \
    -DWITH_AVFOUNDATION=OFF -DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF
  cmake --build "$build" --config Release -j "$JOBS"
  cmake --install "$build" --config Release
  # Merge OpenCV + bundled 3rd-party static libraries into one archive.
  local libs
  libs=$(find "$install/lib" -name "*.a")
  libtool -static -o "$WORK/libopencv-$sdk.a" $libs
}

build_sdk iphoneos
build_sdk iphonesimulator

# Header root is the directory that contains opencv2/ (include/opencv4 on Unix).
headers() { dirname "$(find "$WORK/install-$1/include" -type d -name opencv2 | head -1)"; }
rm -rf "$HERE/Vendor/opencv2.xcframework"
xcodebuild -create-xcframework \
  -library "$WORK/libopencv-iphoneos.a" -headers "$(headers iphoneos)" \
  -library "$WORK/libopencv-iphonesimulator.a" -headers "$(headers iphonesimulator)" \
  -output "$HERE/Vendor/opencv2.xcframework"
echo "Installed $HERE/Vendor/opencv2.xcframework"
