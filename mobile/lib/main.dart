import 'dart:convert';
import 'dart:io';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gal/gal.dart';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_player/video_player.dart';

const kApiBaseUrl = 'https://auto-shorts-maker.onrender.com';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
    ),
  );
  runApp(const AutoShortsApp());
}

class AutoShortsApp extends StatelessWidget {
  const AutoShortsApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI 숏폼 & 릴스 원클릭 자동 제작기',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        useMaterial3: true,
        fontFamily: Platform.isIOS ? '.SF Pro Text' : 'sans-serif',
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFFF4D8D),
          secondary: Color(0xFF7C4DFF),
          surface: Color(0xFF140C24),
        ),
        scaffoldBackgroundColor: const Color(0xFF0B0714),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0x331A1028),
          hintStyle: TextStyle(color: Colors.white.withValues(alpha: 0.35)),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.12)),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.12)),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(16),
            borderSide: const BorderSide(color: Color(0xFFFF4D8D)),
          ),
        ),
      ),
      home: const BootGate(),
    );
  }
}

class BootGate extends StatefulWidget {
  const BootGate({super.key});

  @override
  State<BootGate> createState() => _BootGateState();
}

class _BootGateState extends State<BootGate> {
  bool _ready = false;
  bool _licensed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) {
      return;
    }
    setState(() {
      _licensed = prefs.getBool('licensed') ?? false;
      _ready = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator(color: Color(0xFFFF4D8D))),
      );
    }
    if (!_licensed) {
      return LicenseScreen(onUnlocked: () => setState(() => _licensed = true));
    }
    return const StudioScreen();
  }
}

class LicenseScreen extends StatefulWidget {
  const LicenseScreen({super.key, required this.onUnlocked});

  final VoidCallback onUnlocked;

  @override
  State<LicenseScreen> createState() => _LicenseScreenState();
}

class _LicenseScreenState extends State<LicenseScreen> {
  final _keyCtrl = TextEditingController();
  final _urlCtrl = TextEditingController(text: kApiBaseUrl);
  String _deviceId = '';
  String _platform = '';
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadDevice();
  }

  @override
  void dispose() {
    _keyCtrl.dispose();
    _urlCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadDevice() async {
    final info = DeviceInfoPlugin();
    String id = '';
    String plat = '';
    if (Platform.isAndroid) {
      final android = await info.androidInfo;
      id = android.id;
      plat = 'android';
    } else if (Platform.isIOS) {
      final ios = await info.iosInfo;
      id = ios.identifierForVendor ?? '';
      plat = 'ios';
    }
    final prefs = await SharedPreferences.getInstance();
    final savedUrl = prefs.getString('server_url');
    if (!mounted) {
      return;
    }
    setState(() {
      _deviceId = id;
      _platform = plat;
      if (savedUrl != null &&
          savedUrl.isNotEmpty &&
          !savedUrl.contains('192.168.') &&
          !savedUrl.contains('localhost')) {
        _urlCtrl.text = savedUrl;
      } else {
        _urlCtrl.text = kApiBaseUrl;
      }
    });
  }

  Future<void> _verify() async {
    final key = _keyCtrl.text.trim();
    final url = _urlCtrl.text.trim().replaceAll(RegExp(r'/$'), '');
    if (key.isEmpty) {
      setState(() => _error = '라이선스 키를 입력해 주세요.');
      return;
    }
    if (_deviceId.isEmpty) {
      setState(() => _error = '기기 번호를 읽지 못했습니다. 권한을 확인해 주세요.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final res = await http
          .post(
            Uri.parse('$url/verify-license'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'license_key': key,
              'device_id': _deviceId,
              'platform': _platform,
            }),
          )
          .timeout(const Duration(seconds: 20));
      if (res.statusCode != 200) {
        throw _apiError(res);
      }
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('licensed', true);
      await prefs.setString('license_key', key);
      await prefs.setString('device_id', _deviceId);
      await prefs.setString('platform', _platform);
      await prefs.setString('server_url', url);
      widget.onUnlocked();
    } catch (e) {
      setState(() => _error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(-0.8, -1.1),
            radius: 1.2,
            colors: [Color(0x55FF4D8D), Color(0xFF0B0714)],
          ),
        ),
        child: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(22, 28, 22, 28),
            children: [
              const _Hero(
                badge: 'LICENSE LOCK · 1 PHONE',
                title: '라이선스 인증',
                subtitle: '라이선스 키를 입력하면 이 스마트폰 고유번호로 1대만 자동 인증됩니다.',
              ),
              const SizedBox(height: 22),
              const _Label('서버 주소 (PC IP)'),
              TextField(
                controller: _urlCtrl,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  hintText: 'https://auto-shorts-maker.onrender.com',
                ),
              ),
              const SizedBox(height: 16),
              const _Label('이 폰 고유 기기번호 (자동)'),
              _ChipBox(
                text: _deviceId.isEmpty ? '기기번호를 읽는 중...' : _deviceId,
              ),
              const SizedBox(height: 8),
              Text(
                _platform.isEmpty ? '' : '플랫폼: $_platform',
                style: TextStyle(color: Colors.white.withValues(alpha: 0.45), fontSize: 12),
              ),
              const SizedBox(height: 16),
              const _Label('라이선스 키'),
              TextField(
                controller: _keyCtrl,
                obscureText: true,
                decoration: const InputDecoration(
                  hintText: 'ASM-XXXX-XXXX-XXXX-XXXX',
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 14),
                Text(_error!, style: const TextStyle(color: Color(0xFFFF8AA8))),
              ],
              const SizedBox(height: 22),
              _PrimaryButton(
                label: _busy ? '인증 중...' : '라이선스 인증하고 시작하기',
                onPressed: _busy ? null : _verify,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class StudioScreen extends StatefulWidget {
  const StudioScreen({super.key});

  @override
  State<StudioScreen> createState() => _StudioScreenState();
}

class _StudioScreenState extends State<StudioScreen> {
  final _styleCtrl = TextEditingController(text: '신나는 브이로그');
  final _picker = ImagePicker();
  final _presets = const ['신나는 브이로그', '감동적인 일상', '빠른 템포의 유머 숏폼', '감성 힐링 여행'];
  List<XFile> _media = [];
  bool _busy = false;
  String _busyText = '릴스를 만들고 있어요...';
  File? _resultVideo;
  VideoPlayerController? _player;

  @override
  void dispose() {
    _styleCtrl.dispose();
    _player?.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final picked = await _picker.pickMultipleMedia();
    if (picked.isEmpty) {
      return;
    }
    setState(() => _media = picked);
  }

  Future<void> _create() async {
    if (_media.isEmpty) {
      _toast('갤러리에서 사진 또는 동영상을 선택해 주세요.');
      return;
    }
    if (_styleCtrl.text.trim().isEmpty) {
      _toast('스타일 프롬프트를 입력해 주세요.');
      return;
    }
    final prefs = await SharedPreferences.getInstance();
    var url = (prefs.getString('server_url') ?? kApiBaseUrl).replaceAll(RegExp(r'/$'), '');
    if (url.isEmpty || url.contains('192.168.') || url.contains('localhost')) {
      url = kApiBaseUrl;
    }
    final key = prefs.getString('license_key') ?? '';
    final deviceId = prefs.getString('device_id') ?? '';
    final platform = prefs.getString('platform') ?? '';
    if (url.isEmpty || key.isEmpty || deviceId.isEmpty) {
      _toast('라이선스 정보가 없습니다. 앱을 다시 시작해 주세요.');
      return;
    }

    setState(() {
      _busy = true;
      _busyText = '미디어를 올리고 릴스를 제작 중입니다.\n1~3분 걸릴 수 있어요.';
    });

    try {
      final req = http.MultipartRequest('POST', Uri.parse('$url/create-video'));
      req.fields['style'] = _styleCtrl.text.trim();
      req.fields['license_key'] = key;
      req.fields['device_id'] = deviceId;
      req.fields['platform'] = platform;
      for (final file in _media) {
        req.files.add(
          await http.MultipartFile.fromPath(
            'files',
            file.path,
            filename: p.basename(file.path),
          ),
        );
      }
      final res = await Future(() async {
        final streamed = await req.send();
        return http.Response.fromStream(streamed);
      }).timeout(const Duration(seconds: 180));
      if (res.statusCode != 200) {
        throw _apiError(res);
      }
      final dir = await getTemporaryDirectory();
      final out = File('${dir.path}/final_shorts_${DateTime.now().millisecondsSinceEpoch}.mp4');
      await out.writeAsBytes(res.bodyBytes, flush: true);
      await _player?.dispose();
      final player = VideoPlayerController.file(out);
      await player.initialize();
      await player.setLooping(true);
      await player.play();
      if (!mounted) {
        return;
      }
      setState(() {
        _resultVideo = out;
        _player = player;
      });
    } catch (e) {
      if (mounted) {
        _toast(e.toString().replaceFirst('Exception: ', ''));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _save() async {
    final file = _resultVideo;
    if (file == null) {
      return;
    }
    try {
      await Gal.putVideo(file.path);
      if (mounted) {
        _toast('갤러리에 저장했습니다.');
      }
    } catch (e) {
      if (mounted) {
        _toast('저장 실패: $e');
      }
    }
  }

  void _toast(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg), behavior: SnackBarBehavior.floating),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          Container(
            decoration: const BoxDecoration(
              gradient: RadialGradient(
                center: Alignment(1.0, -1.0),
                radius: 1.15,
                colors: [Color(0x447C4DFF), Color(0xFF0B0714)],
              ),
            ),
            child: SafeArea(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(22, 18, 22, 28),
                children: [
                  const _Hero(
                    badge: 'YouTube Shorts · Instagram Reels',
                    title: 'AI 숏폼 & 릴스\n원클릭 자동 제작기',
                    subtitle: '사진과 영상을 올리면 대본·보이스·자막까지 세로 숏폼으로 만들어 줍니다.',
                  ),
                  const SizedBox(height: 20),
                  const _Label('1. 갤러리에서 사진 / 동영상 선택'),
                  OutlinedButton.icon(
                    onPressed: _busy ? null : _pick,
                    icon: const Icon(Icons.photo_library_outlined),
                    label: Text(_media.isEmpty ? '여러 장 선택하기' : '${_media.length}개 선택됨'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      side: BorderSide(color: Colors.white.withValues(alpha: 0.2)),
                      minimumSize: const Size.fromHeight(52),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                    ),
                  ),
                  if (_media.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    SizedBox(
                      height: 84,
                      child: ListView.separated(
                        scrollDirection: Axis.horizontal,
                        itemCount: _media.length,
                        separatorBuilder: (_, _) => const SizedBox(width: 8),
                        itemBuilder: (_, i) {
                          final path = _media[i].path.toLowerCase();
                          final isVideo = path.endsWith('.mp4') ||
                              path.endsWith('.mov') ||
                              path.endsWith('.m4v') ||
                              path.endsWith('.webm');
                          return ClipRRect(
                            borderRadius: BorderRadius.circular(12),
                            child: Container(
                              width: 84,
                              color: const Color(0x331A1028),
                              child: isVideo
                                  ? const Center(child: Icon(Icons.videocam, size: 28))
                                  : Image.file(File(_media[i].path), fit: BoxFit.cover),
                            ),
                          );
                        },
                      ),
                    ),
                  ],
                  const SizedBox(height: 20),
                  const _Label('2. 스타일 프롬프트'),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _presets
                        .map(
                          (preset) => ActionChip(
                            label: Text(preset),
                            onPressed: () => setState(() => _styleCtrl.text = preset),
                            backgroundColor: const Color(0x331A1028),
                            side: BorderSide(color: Colors.white.withValues(alpha: 0.12)),
                          ),
                        )
                        .toList(),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: _styleCtrl,
                    minLines: 2,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      hintText: "예: 신나는 브이로그, 감동적인 일상",
                    ),
                  ),
                  const SizedBox(height: 22),
                  _PrimaryButton(
                    label: '원클릭 릴스 제작하기',
                    onPressed: _busy ? null : _create,
                  ),
                  if (_player != null && _player!.value.isInitialized) ...[
                    const SizedBox(height: 28),
                    const _Label('완성된 숏폼'),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(18),
                      child: AspectRatio(
                        aspectRatio: 9 / 16,
                        child: Stack(
                          alignment: Alignment.center,
                          children: [
                            VideoPlayer(_player!),
                            Positioned(
                              bottom: 12,
                              child: IconButton.filled(
                                onPressed: () {
                                  setState(() {
                                    if (_player!.value.isPlaying) {
                                      _player!.pause();
                                    } else {
                                      _player!.play();
                                    }
                                  });
                                },
                                icon: Icon(
                                  _player!.value.isPlaying ? Icons.pause : Icons.play_arrow,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                    FilledButton.icon(
                      onPressed: _save,
                      icon: const Icon(Icons.save_alt),
                      label: const Text('폰 갤러리에 저장하기'),
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.white,
                        foregroundColor: const Color(0xFF1A1028),
                        minimumSize: const Size.fromHeight(52),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          if (_busy) _LoadingMask(text: _busyText),
        ],
      ),
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({required this.badge, required this.title, required this.subtitle});

  final String badge;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.08),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
          ),
          child: Text(
            badge,
            style: const TextStyle(color: Color(0xFFFFD3EA), fontSize: 12, fontWeight: FontWeight.w600),
          ),
        ),
        const SizedBox(height: 14),
        Text(
          title,
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800, height: 1.25),
        ),
        const SizedBox(height: 10),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: TextStyle(color: Colors.white.withValues(alpha: 0.7), height: 1.4),
        ),
      ],
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(text, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
    );
  }
}

class _ChipBox extends StatelessWidget {
  const _ChipBox({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.28),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
      ),
      child: Text(
        text,
        style: const TextStyle(
          fontFamily: 'monospace',
          color: Color(0xFFFFD3EA),
          fontSize: 13,
        ),
      ),
    );
  }
}

class _PrimaryButton extends StatelessWidget {
  const _PrimaryButton({required this.label, required this.onPressed});
  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: const LinearGradient(colors: [Color(0xFFFF4D8D), Color(0xFF7C4DFF)]),
        borderRadius: BorderRadius.circular(16),
      ),
      child: ElevatedButton(
        onPressed: onPressed,
        style: ElevatedButton.styleFrom(
          backgroundColor: Colors.transparent,
          shadowColor: Colors.transparent,
          minimumSize: const Size.fromHeight(56),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        ),
        child: Text(label, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
      ),
    );
  }
}

class _LoadingMask extends StatelessWidget {
  const _LoadingMask({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: Colors.black.withValues(alpha: 0.62),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(color: Color(0xFFFF4D8D)),
            const SizedBox(height: 18),
            Text(
              text,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 15, height: 1.4),
            ),
          ],
        ),
      ),
    );
  }
}

String _apiError(http.Response res) {
  try {
    final decoded = jsonDecode(res.body);
    final detail = decoded is Map ? decoded['detail'] : decoded;
    if (detail is String) {
      return detail;
    }
    if (detail is List) {
      return detail.map((e) => e.toString()).join('\n');
    }
  } catch (_) {}
  if (res.body.isNotEmpty) {
    return res.body;
  }
  return '서버 오류 (${res.statusCode})';
}
