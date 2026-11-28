import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;
import 'dart:math' as math;

/// Bounded local image editing. No network, storage, or mutation of the source.
class LogoPixels {
  LogoPixels(this.width, this.height, Uint8List pixels)
    : rgba = Uint8List.fromList(pixels) {
    if (width < 1 ||
        height < 1 ||
        width > 512 ||
        height > 512 ||
        rgba.length != width * height * 4) {
      throw const FormatException('Invalid logo dimensions.');
    }
  }
  final int width, height;
  final Uint8List rgba;

  static Future<LogoPixels> decode(Uint8List bytes) async {
    if (bytes.isEmpty || bytes.length > 2 * 1024 * 1024) {
      throw const FormatException('Logo must be at most 2 MiB.');
    }
    final buffer = await ui.ImmutableBuffer.fromUint8List(bytes);
    try {
      final descriptor = await ui.ImageDescriptor.encoded(buffer);
      try {
        if (descriptor.width < 1 ||
            descriptor.height < 1 ||
            descriptor.width > 4096 ||
            descriptor.height > 4096) {
          throw const FormatException(
            'Logo dimensions must not exceed 4096 pixels.',
          );
        }
        final scale = math.min(
          1.0,
          512 / math.max(descriptor.width, descriptor.height),
        );
        final codec = await descriptor.instantiateCodec(
          targetWidth: math.max(1, (descriptor.width * scale).round()),
          targetHeight: math.max(1, (descriptor.height * scale).round()),
        );
        try {
          if (codec.frameCount != 1) {
            throw const FormatException('Choose a single-frame PNG or JPEG.');
          }
          final frame = await codec.getNextFrame();
          try {
            final data = await frame.image.toByteData(
              format: ui.ImageByteFormat.rawStraightRgba,
            );
            if (data == null) {
              throw const FormatException('Logo cannot be decoded.');
            }
            return LogoPixels(
              frame.image.width,
              frame.image.height,
              data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
            );
          } finally {
            frame.image.dispose();
          }
        } finally {
          codec.dispose();
        }
      } finally {
        descriptor.dispose();
      }
    } finally {
      buffer.dispose();
    }
  }

  int rgbAt(int x, int y) {
    if (x < 0 || x >= width || y < 0 || y >= height) {
      throw const FormatException('Invalid sample position.');
    }
    final i = (y * width + x) * 4;
    return (rgba[i] << 16) | (rgba[i + 1] << 8) | rgba[i + 2];
  }

  /// Flood only matching pixels reachable from an outside edge. Disconnected
  /// foreground details survive; existing alpha is never made more opaque.
  LogoPixels edit({
    required bool remove,
    required int background,
    required int tolerance,
    required bool crop,
  }) {
    if (background < 0 ||
        background > 0xffffff ||
        tolerance < 0 ||
        tolerance > 100) {
      throw const FormatException('Invalid background removal settings.');
    }
    final out = Uint8List.fromList(rgba);
    if (remove) {
      final visited = Uint8List(width * height);
      final queue = Int32List(width * height);
      var tail = 0, head = 0;
      final r = background >> 16,
          g = (background >> 8) & 255,
          b = background & 255;
      void enqueue(int p) {
        if (visited[p] != 0) {
          return;
        }
        visited[p] = 1;
        final i = p * 4;
        if (out[i + 3] == 0 ||
            ((out[i] - r).abs() <= tolerance &&
                (out[i + 1] - g).abs() <= tolerance &&
                (out[i + 2] - b).abs() <= tolerance)) {
          queue[tail++] = p;
        }
      }

      for (var x = 0; x < width; x++) {
        enqueue(x);
        enqueue((height - 1) * width + x);
      }
      for (var y = 0; y < height; y++) {
        enqueue(y * width);
        enqueue(y * width + width - 1);
      }
      while (head < tail) {
        final p = queue[head++], x = p % width, y = p ~/ width;
        out.fillRange(p * 4, p * 4 + 4, 0);
        if (x > 0) {
          enqueue(p - 1);
        }
        if (x + 1 < width) {
          enqueue(p + 1);
        }
        if (y > 0) {
          enqueue(p - width);
        }
        if (y + 1 < height) {
          enqueue(p + width);
        }
      }
    }
    var left = width, right = -1, top = height, bottom = -1;
    for (var y = 0; y < height; y++) {
      for (var x = 0; x < width; x++) {
        if (out[(y * width + x) * 4 + 3] == 0) {
          continue;
        }
        left = math.min(left, x);
        right = math.max(right, x);
        top = math.min(top, y);
        bottom = math.max(bottom, y);
      }
    }
    if (right < left || bottom < top) {
      throw const FormatException(
        'No visible logo remains. Reduce tolerance or restore original.',
      );
    }
    if (!crop) {
      return LogoPixels(width, height, out);
    }
    final w = right - left + 1, h = bottom - top + 1;
    final trimmed = Uint8List(w * h * 4);
    for (var y = 0; y < h; y++) {
      final start = ((top + y) * width + left) * 4;
      trimmed.setRange(y * w * 4, (y + 1) * w * 4, out, start);
    }
    return LogoPixels(w, h, trimmed);
  }

  /// A deterministic RGBA PNG using filter 0 and zlib; no metadata or disk.
  Uint8List png() {
    Uint8List be(int n) => (ByteData(4)..setUint32(0, n)).buffer.asUint8List();
    int crc(List<int> data) {
      var c = 0xffffffff;
      for (final v in data) {
        c ^= v;
        for (var j = 0; j < 8; j++) {
          c = (c >>> 1) ^ ((c & 1) != 0 ? 0xedb88320 : 0);
        }
      }
      return (c ^ 0xffffffff) & 0xffffffff;
    }

    final result = BytesBuilder()..add(const [137, 80, 78, 71, 13, 10, 26, 10]);
    void chunk(String kind, List<int> bytes) {
      final body = <int>[...kind.codeUnits, ...bytes];
      result.add(be(bytes.length));
      result.add(body);
      result.add(be(crc(body)));
    }

    chunk('IHDR', [...be(width), ...be(height), 8, 6, 0, 0, 0]);
    final scan = Uint8List((width * 4 + 1) * height);
    for (var y = 0; y < height; y++) {
      final start = y * (width * 4 + 1) + 1;
      scan.setRange(start, start + width * 4, rgba, y * width * 4);
    }
    chunk('IDAT', zlib.encode(scan));
    chunk('IEND', const []);
    return result.takeBytes();
  }
}

/// Matches the promoted backend's aspect-preserving maximum dimensions.
ui.Size logoFit(int width, int height, String size) {
  if (width < 1 || height < 1) {
    throw const FormatException('Invalid logo dimensions.');
  }
  final limits = switch (size) {
    'small' => const ui.Size(100, 48),
    'medium' => const ui.Size(120, 64),
    'large' => const ui.Size(144, 80),
    _ => throw const FormatException('Unsupported logo size.'),
  };
  final scale = math.min(limits.width / width, limits.height / height);
  return ui.Size(width * scale, height * scale);
}
