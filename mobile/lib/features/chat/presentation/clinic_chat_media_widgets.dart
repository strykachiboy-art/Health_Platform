import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:just_audio/just_audio.dart';
import 'package:record/record.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_media_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

const bool clinicChatMediaBuildEnabled = bool.fromEnvironment(
  'CLINIC_CHAT_MEDIA_ENABLED',
  defaultValue: false,
);

class ClinicChatMediaComposer extends StatefulWidget {
  const ClinicChatMediaComposer({
    super.key,
    required this.source,
    required this.conversationId,
    required this.enabled,
    required this.onConfirmed,
    required this.onOutcomeUnconfirmed,
  });

  final ClinicChatMediaRemoteDataSource source;
  final int conversationId;
  final bool enabled;
  final VoidCallback onConfirmed;
  final VoidCallback onOutcomeUnconfirmed;

  @override
  State<ClinicChatMediaComposer> createState() =>
      _ClinicChatMediaComposerState();
}

class _ClinicChatMediaComposerState extends State<ClinicChatMediaComposer> {
  ClinicChatMediaSelection? _selection;
  AudioRecorder? _recorder;
  StreamSubscription<Uint8List>? _chunks;
  Timer? _recordLimit;
  BytesBuilder _recording = BytesBuilder(copy: false);
  bool _busy = false;
  bool _recordingNow = false;
  bool _locked = false;
  String? _notice;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.read.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  @override
  void didUpdateWidget(covariant ClinicChatMediaComposer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.source.read.sessionNavigationGuard,
      widget.source.read.sessionNavigationGuard,
    )) {
      oldWidget.source.read.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.source.read.sessionNavigationGuard.addListener(_onSessionChanged);
      _clear();
    }
    if (oldWidget.conversationId != widget.conversationId ||
        oldWidget.enabled != widget.enabled) {
      _clear();
    }
  }

  void _wipe(Uint8List? bytes) {
    bytes?.fillRange(0, bytes.length, 0);
  }

  void _clear() {
    _generation++;
    _recordLimit?.cancel();
    _recordLimit = null;
    unawaited(_recorder?.cancel());
    unawaited(_chunks?.cancel());
    _chunks = null;
    _recordingNow = false;
    _wipe(_selection?.bytes);
    _selection = null;
    _recording = BytesBuilder(copy: false);
    _notice = null;
    _busy = false;
  }

  void _onSessionChanged() {
    if (!mounted) return;
    setState(_clear);
  }

  @override
  void dispose() {
    widget.source.read.sessionNavigationGuard.removeListener(_onSessionChanged);
    _clear();
    final recorder = _recorder;
    if (recorder != null) unawaited(recorder.dispose());
    super.dispose();
  }

  bool get _canUse =>
      mounted &&
      widget.enabled &&
      !_busy &&
      !_locked &&
      widget.source.read.sessionNavigationGuard.canAccessProtectedRoutes &&
      !widget.source.outcomeUnconfirmed;

  void _select(ClinicChatMediaSelection item, int generation) {
    if (!mounted || generation != _generation || !_canUse) {
      _wipe(item.bytes);
      return;
    }
    try {
      ClinicChatMediaRemoteDataSource.validate(item);
      setState(() {
        _wipe(_selection?.bytes);
        _selection = item;
        _notice = null;
      });
    } on Object {
      _wipe(item.bytes);
      setState(() => _notice = 'Unsupported or oversized media.');
    }
  }

  Future<void> _image(ImageSource source) async {
    if (!_canUse) return;
    final generation = _generation;
    try {
      final file = await ImagePicker().pickImage(
        source: source,
        imageQuality: 85,
      );
      if (file == null) return;
      final data = await file.readAsBytes();
      final mime = _imageMime(data);
      if (mime == null) {
        _wipe(data);
        if (mounted && generation == _generation) {
          setState(() => _notice = 'Image format not supported.');
        }
        return;
      }
      final suffix = mime == 'image/png'
          ? '.png'
          : mime == 'image/webp'
          ? '.webp'
          : '.jpg';
      _select(
        ClinicChatMediaSelection(
          kind: 'image',
          name: 'clinical-image$suffix',
          mime: mime,
          bytes: data,
        ),
        generation,
      );
    } on Object {
      if (mounted && generation == _generation) {
        setState(() => _notice = 'Image selection unavailable.');
      }
    }
  }

  static String? _imageMime(Uint8List data) {
    if (data.length >= 8 &&
        data[0] == 137 &&
        data[1] == 80 &&
        data[2] == 78 &&
        data[3] == 71) {
      return 'image/png';
    }
    if (data.length > 4 &&
        data[0] == 0xff &&
        data[1] == 0xd8 &&
        data[2] == 0xff &&
        data[data.length - 2] == 0xff &&
        data.last == 0xd9) {
      return 'image/jpeg';
    }
    if (data.length > 12 &&
        String.fromCharCodes(data.sublist(0, 4)) == 'RIFF' &&
        String.fromCharCodes(data.sublist(8, 12)) == 'WEBP') {
      return 'image/webp';
    }
    return null;
  }

  Future<void> _document() async {
    if (!_canUse) return;
    final generation = _generation;
    try {
      final file = await FilePicker.pickFile(
        type: FileType.custom,
        allowedExtensions: ['pdf', 'docx'],
      );
      if (file == null) return;
      final data = await file.readAsBytes();
      final lower = file.name.toLowerCase();
      final mime = lower.endsWith('.pdf')
          ? 'application/pdf'
          : lower.endsWith('.docx')
          ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
          : null;
      if (mime == null) {
        _wipe(data);
        return;
      }
      _select(
        ClinicChatMediaSelection(
          kind: 'document',
          name: file.name,
          mime: mime,
          bytes: data,
        ),
        generation,
      );
    } on Object {
      if (mounted && generation == _generation) {
        setState(() => _notice = 'Document selection unavailable.');
      }
    }
  }

  Future<void> _startRecording() async {
    if (!_canUse || _selection != null || _recordingNow) return;
    try {
      final recorder = _recorder ??= AudioRecorder();
      if (!await recorder.hasPermission()) {
        if (mounted) setState(() => _notice = 'Microphone permission denied.');
        return;
      }
      _recording = BytesBuilder(copy: false);
      final chunks = await recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
        ),
      );
      _chunks = chunks.listen((part) {
        _recording.add(part);
        if (_recording.length > 5 * 1024 * 1024) {
          unawaited(_stopRecording());
        }
      });
      if (!mounted || !_canUse) {
        await recorder.cancel();
        return;
      }
      setState(() {
        _recordingNow = true;
        _notice = null;
      });
      _recordLimit = Timer(const Duration(seconds: 150), () {
        unawaited(_stopRecording());
      });
    } on Object {
      if (mounted) setState(() => _notice = 'Voice recorder unavailable.');
    }
  }

  static Uint8List _wave(Uint8List pcm) {
    final header = ByteData(44);
    void four(int offset, String text) {
      for (var i = 0; i < 4; i++) {
        header.setUint8(offset + i, text.codeUnitAt(i));
      }
    }

    four(0, 'RIFF');
    header.setUint32(4, 36 + pcm.length, Endian.little);
    four(8, 'WAVE');
    four(12, 'fmt ');
    header.setUint32(16, 16, Endian.little);
    header.setUint16(20, 1, Endian.little);
    header.setUint16(22, 1, Endian.little);
    header.setUint32(24, 16000, Endian.little);
    header.setUint32(28, 32000, Endian.little);
    header.setUint16(32, 2, Endian.little);
    header.setUint16(34, 16, Endian.little);
    four(36, 'data');
    header.setUint32(40, pcm.length, Endian.little);
    return Uint8List.fromList([...header.buffer.asUint8List(), ...pcm]);
  }

  Future<void> _stopRecording() async {
    if (!_recordingNow) return;
    final generation = _generation;
    _recordLimit?.cancel();
    _recordLimit = null;
    setState(() => _recordingNow = false);
    try {
      await _recorder?.stop();
      await _chunks?.cancel();
      _chunks = null;
      final raw = _recording.takeBytes();
      _recording = BytesBuilder(copy: false);
      if (raw.length < 3200 || raw.length > 5 * 1024 * 1024) {
        _wipe(raw);
        if (mounted) {
          setState(() => _notice = 'Recording length not supported.');
        }
        return;
      }
      final wav = _wave(raw);
      _wipe(raw);
      _select(
        ClinicChatMediaSelection(
          kind: 'audio',
          name: 'clinical-voice.wav',
          mime: 'audio/wav',
          bytes: wav,
        ),
        generation,
      );
    } on Object {
      if (mounted) {
        setState(() => _notice = 'Recording could not be completed.');
      }
    }
  }

  Future<void> _send() async {
    final item = _selection;
    if (!_canUse || item == null) return;
    final generation = _generation;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await widget.source.upload(
        conversationId: widget.conversationId,
        selection: item,
        requestId: Uuid().v4(),
      );
      if (mounted && generation == _generation) {
        setState(() {
          _wipe(item.bytes);
          _selection = null;
          _busy = false;
          _notice = 'Media confirmed by server.';
        });
        widget.onConfirmed();
      }
    } on ClinicChatMediaOutcomeUnconfirmed {
      if (mounted && generation == _generation) {
        setState(() {
          _locked = true;
          _busy = false;
          _notice = 'Upload outcome unconfirmed. Do not resend.';
        });
        widget.onOutcomeUnconfirmed();
      }
    } on Object {
      if (mounted && generation == _generation) {
        setState(() {
          _busy = false;
          _notice =
              'Media upload rejected or unavailable. Review before retrying.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final selected = _selection;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Wrap(
          spacing: 8,
          children: [
            IconButton(
              key: const ValueKey('chat-f-camera'),
              tooltip: 'Capture clinical photo',
              onPressed: _canUse && !_recordingNow
                  ? () => _image(ImageSource.camera)
                  : null,
              icon: const Icon(Icons.photo_camera_outlined),
            ),
            IconButton(
              key: const ValueKey('chat-f-gallery'),
              tooltip: 'Select clinical image',
              onPressed: _canUse && !_recordingNow
                  ? () => _image(ImageSource.gallery)
                  : null,
              icon: const Icon(Icons.photo_library_outlined),
            ),
            IconButton(
              key: const ValueKey('chat-f-document'),
              tooltip: 'Select PDF or DOCX document',
              onPressed: _canUse && !_recordingNow ? _document : null,
              icon: const Icon(Icons.attach_file),
            ),
            IconButton(
              key: const ValueKey('chat-f-record'),
              tooltip: _recordingNow ? 'Stop recording' : 'Record voice note',
              onPressed: _canUse && selected == null
                  ? (_recordingNow ? _stopRecording : _startRecording)
                  : null,
              icon: Icon(
                _recordingNow ? Icons.stop_circle_outlined : Icons.mic_none,
              ),
            ),
          ],
        ),
        if (_recordingNow)
          const Text('Recording voice note · maximum 150 seconds'),
        if (selected != null) ...[
          Text(
            'Selected ${selected.kind} · ${selected.bytes.length} bytes',
            key: const ValueKey('chat-f-selection'),
          ),
          if (selected.kind == 'image')
            Image.memory(
              selected.bytes,
              width: 96,
              height: 96,
              fit: BoxFit.contain,
              errorBuilder: (_, _, _) => const Text('Preview unavailable'),
            ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                key: const ValueKey('chat-f-cancel'),
                onPressed: _canUse
                    ? () => setState(() {
                        _wipe(_selection?.bytes);
                        _selection = null;
                      })
                    : null,
                child: const Text('Remove'),
              ),
              FilledButton(
                key: const ValueKey('chat-f-upload'),
                onPressed: _canUse ? _send : null,
                child: const Text('Send attachment'),
              ),
            ],
          ),
        ],
        if (_busy) const LinearProgressIndicator(),
        if (_notice != null)
          Text(_notice!, key: const ValueKey('chat-f-media-status')),
      ],
    );
  }
}

// ignore: experimental_member_use
class _MemoryAudioSource extends StreamAudioSource {
  _MemoryAudioSource(this.bytes, this.mime);
  final Uint8List bytes;
  final String mime;

  @override
  // ignore: experimental_member_use
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    final begin = (start ?? 0).clamp(0, bytes.length).toInt();
    final finish = (end ?? bytes.length).clamp(begin, bytes.length).toInt();
    // ignore: experimental_member_use
    return StreamAudioResponse(
      sourceLength: bytes.length,
      contentLength: finish - begin,
      offset: begin,
      stream: Stream.value(bytes.sublist(begin, finish)),
      contentType: mime,
    );
  }
}

class ClinicChatMediaAttachmentTile extends StatefulWidget {
  const ClinicChatMediaAttachmentTile({
    super.key,
    required this.source,
    required this.message,
    required this.attachment,
  });
  final ClinicChatMediaRemoteDataSource source;
  final ClinicChatMessage message;
  final ClinicChatAttachment attachment;

  @override
  State<ClinicChatMediaAttachmentTile> createState() =>
      _ClinicChatMediaAttachmentTileState();
}

class _ClinicChatMediaAttachmentTileState
    extends State<ClinicChatMediaAttachmentTile> {
  AudioPlayer? _player;
  Uint8List? _bytes;
  bool _busy = false;
  String? _notice;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.read.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  void _clear() {
    _generation++;
    unawaited(_player?.stop());
    unawaited(_player?.dispose());
    _player = null;
    _bytes?.fillRange(0, _bytes!.length, 0);
    _bytes = null;
    _notice = null;
    _busy = false;
  }

  void _onSessionChanged() {
    if (mounted) setState(_clear);
  }

  @override
  void didUpdateWidget(covariant ClinicChatMediaAttachmentTile oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.source.read.sessionNavigationGuard,
      widget.source.read.sessionNavigationGuard,
    )) {
      oldWidget.source.read.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.source.read.sessionNavigationGuard.addListener(_onSessionChanged);
    }
    if (oldWidget.attachment.id != widget.attachment.id ||
        oldWidget.message.id != widget.message.id) {
      _clear();
    }
  }

  @override
  void dispose() {
    widget.source.read.sessionNavigationGuard.removeListener(_onSessionChanged);
    _clear();
    super.dispose();
  }

  Future<void> _open() async {
    if (_busy ||
        !widget.source.read.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final generation = _generation;
    setState(() => _busy = true);
    try {
      final data = await widget.source.download(
        message: widget.message,
        attachment: widget.attachment,
      );
      if (!mounted || generation != _generation) {
        data.fillRange(0, data.length, 0);
        return;
      }
      _bytes?.fillRange(0, _bytes!.length, 0);
      _bytes = data;
      if (widget.attachment.mimeType.startsWith('audio/')) {
        final player = _player ??= AudioPlayer();
        await player.setAudioSource(
          _MemoryAudioSource(data, widget.attachment.mimeType),
        );
        if (mounted && generation == _generation) unawaited(player.play());
      } else if (widget.attachment.mimeType == 'application/pdf' ||
          widget.attachment.mimeType.contains('wordprocessingml')) {
        // Do not hand protected PHI to an unverified external viewer.
        _bytes?.fillRange(0, _bytes!.length, 0);
        _bytes = null;
        _notice = 'Document verified. Secure in-app preview is not enabled.';
      }
      if (mounted && generation == _generation) setState(() => _busy = false);
    } on Object {
      if (mounted && generation == _generation) {
        setState(() {
          _busy = false;
          _notice = 'Attachment unavailable or access changed.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    children: [
      TextButton.icon(
        key: ValueKey('chat-f-download-${widget.attachment.id}'),
        onPressed: _busy ? null : _open,
        icon: const Icon(Icons.lock_outline),
        label: Text(
          widget.attachment.mimeType.startsWith('audio/')
              ? 'Play authorized voice note'
              : 'View protected attachment',
        ),
      ),
      if (_busy) const LinearProgressIndicator(),
      if (_bytes != null && widget.attachment.mimeType.startsWith('image/'))
        Image.memory(
          _bytes!,
          width: 180,
          height: 140,
          fit: BoxFit.contain,
          errorBuilder: (_, _, _) => const Text('Image unavailable'),
        ),
      if (_notice != null) Text(_notice!),
    ],
  );
}
