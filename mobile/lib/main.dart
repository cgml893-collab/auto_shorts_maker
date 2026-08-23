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
      Object? last;
      http.Response? res;
      for (var i = 0; i < 5; i++) {
        try {
          res = await http
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
          if (res.statusCode == 200) {
            last = null;
            break;
          }
          if (res.statusCode >= 400 && res.statusCode < 500 && res.statusCode != 429) {
            throw Exception(_apiError(res));
          }
          last = Exception(_apiError(res));
        } catch (e) {
          last = e;
        }
        await Future.delayed(Duration(milliseconds: 400 * (i + 1)));
      }
      if (res == null || res.statusCode != 200) {
        throw last ?? Exception('인증 실패');
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
  List<XFile> _media = [];
  bool _busy = false;
  String _busyText = '릴스를 만들고 있어요...';
  double _progress = 0;
  String _voiceType = 'vlog_female';
  String _speed = '1.2';
  String _bgmMood = 'pop';
  List<Map<String, String>> _aiStyles = [];
  bool _analyzing = false;
  bool _runwayMode = false;
  String _cameraMotion = 'zoom_in';
  File? _resultVideo;
  VideoPlayerController? _player;

  @override
  void dispose() {
    _styleCtrl.dispose();
    _player?.dispose();
    super.dispose();
  }

  Future<Map<String, String>> _apiAuth() async {
    final prefs = await SharedPreferences.getInstance();
    var url = (prefs.getString('server_url') ?? kApiBaseUrl).replaceAll(RegExp(r'/$'), '');
    if (url.isEmpty || url.contains('192.168.') || url.contains('localhost')) {
      url = kApiBaseUrl;
    }
    return {
      'url': url,
      'license_key': prefs.getString('license_key') ?? '',
      'device_id': prefs.getString('device_id') ?? '',
      'platform': prefs.getString('platform') ?? '',
    };
  }

  Future<void> _pick() async {
    final picked = await _picker.pickMultipleMedia();
    if (picked.isEmpty) {
      return;
    }
    setState(() {
      _media = picked;
      _aiStyles = [];
    });
    _analyzeStyles();
  }

  Future<void> _analyzeStyles() async {
    if (_media.isEmpty) {
      return;
    }
    setState(() => _analyzing = true);
    try {
      final auth = await _apiAuth();
      if (auth['url']!.isEmpty || auth['license_key']!.isEmpty) {
        return;
      }
      http.Response? res;
      Object? last;
      for (var i = 0; i < 5; i++) {
        try {
          final req = http.MultipartRequest('POST', Uri.parse('${auth['url']}/analyze-media'));
          req.fields['license_key'] = auth['license_key']!;
          req.fields['device_id'] = auth['device_id']!;
          req.fields['platform'] = auth['platform']!;
          for (final file in _media.take(2)) {
            req.files.add(
              await http.MultipartFile.fromPath('files', file.path, filename: p.basename(file.path)),
            );
          }
          final streamed = await req.send().timeout(const Duration(seconds: 45));
          res = await http.Response.fromStream(streamed).timeout(const Duration(seconds: 45));
          if (res.statusCode == 200) {
            last = null;
            break;
          }
          if (res.statusCode >= 400 && res.statusCode < 500 && res.statusCode != 429) {
            throw Exception(_apiError(res));
          }
          last = Exception(_apiError(res));
        } catch (e) {
          last = e;
        }
        await Future.delayed(Duration(milliseconds: 400 * (i + 1)));
      }
      if (res == null || res.statusCode != 200) {
        throw last ?? Exception('스타일 추천에 실패했습니다.');
      }
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final raw = body['styles'];
      final styles = <Map<String, String>>[];
      if (raw is List) {
        for (final item in raw) {
          if (item is Map) {
            final label = '${item['label'] ?? ''}'.trim();
            final prompt = '${item['prompt'] ?? label}'.trim();
            if (prompt.isNotEmpty) {
              styles.add({'label': label.isEmpty ? prompt : label, 'prompt': prompt});
            }
          }
        }
      }
      if (!mounted) {
        return;
      }
      setState(() => _aiStyles = styles);
    } catch (_) {
      if (mounted) {
        setState(() => _aiStyles = []);
      }
    } finally {
      if (mounted) {
        setState(() => _analyzing = false);
      }
    }
  }

  Future<http.Response> _retryGet(Uri uri, {Duration timeout = const Duration(seconds: 25)}) async {
    Object? last;
    for (var i = 0; i < 5; i++) {
      try {
        final res = await http.get(uri).timeout(timeout);
        if (res.statusCode >= 400 && res.statusCode < 500 && res.statusCode != 429) {
          return res;
        }
        if (res.statusCode >= 500 || res.statusCode == 429) {
          throw Exception(_apiError(res));
        }
        return res;
      } catch (e) {
        last = e;
        await Future.delayed(Duration(milliseconds: 500 * (i + 1)));
      }
    }
    throw last ?? Exception('네트워크 오류');
  }

  String _stageLabel(String stage) {
    if (stage.contains('런웨이')) {
      return stage;
    }
    if (stage.contains('대본')) {
      return '대본 생성 중';
    }
    if (stage.contains('음성')) {
      return '음성 생성 중';
    }
    if (stage.contains('렌더') || stage.contains('합성') || stage.contains('영상')) {
      return '영상 합성 중';
    }
    if (stage.contains('완료')) {
      return '완료';
    }
    return stage;
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
    final auth = await _apiAuth();
    final url = auth['url']!;
    final key = auth['license_key']!;
    final deviceId = auth['device_id']!;
    final platform = auth['platform']!;
    if (url.isEmpty || key.isEmpty || deviceId.isEmpty) {
      _toast('라이선스 정보가 없습니다. 앱을 다시 시작해 주세요.');
      return;
    }

    setState(() {
      _busy = true;
      _progress = 0.02;
      _busyText = '업로드 중...';
    });

    try {
      http.Response created = http.Response('', 500);
      Object? uploadErr;
      for (var i = 0; i < 5; i++) {
        try {
          final req = http.MultipartRequest('POST', Uri.parse('$url/create-video'));
          req.fields['style'] = _styleCtrl.text.trim();
          req.fields['license_key'] = key;
          req.fields['device_id'] = deviceId;
          req.fields['platform'] = platform;
          req.fields['voice_type'] = _voiceType;
          req.fields['speed_multiplier'] = _speed;
          req.fields['bgm_type'] = _bgmMood;
          req.fields['bgm_mood'] = _bgmMood;
          req.fields['is_runway_mode'] = _runwayMode ? 'true' : 'false';
          req.fields['camera_motion'] = _cameraMotion;
          for (final file in _media) {
            req.files.add(
              await http.MultipartFile.fromPath(
                'files',
                file.path,
                filename: p.basename(file.path),
              ),
            );
          }
          final streamed = await req.send().timeout(const Duration(seconds: 60));
          created = await http.Response.fromStream(streamed).timeout(const Duration(seconds: 60));
          if (created.statusCode == 200) {
            uploadErr = null;
            break;
          }
          if (created.statusCode >= 400 && created.statusCode < 500 && created.statusCode != 429) {
            throw Exception(_apiError(created));
          }
          uploadErr = Exception(_apiError(created));
        } catch (e) {
          uploadErr = e;
        }
        await Future.delayed(Duration(milliseconds: 500 * (i + 1)));
      }
      if (created.statusCode != 200) {
        throw uploadErr ?? Exception('작업 요청에 실패했습니다.');
      }
      final body = jsonDecode(created.body) as Map<String, dynamic>;
      final jobId = (body['job_id'] ?? '').toString();
      if (jobId.isEmpty) {
        throw Exception('job_id를 받지 못했습니다.');
      }

      String status = 'processing';
      var polls = 0;
      while (status == 'processing') {
        if (polls >= 300) {
          throw Exception('작업이 너무 오래 걸립니다. 잠시 후 다시 시도해 주세요.');
        }
        if (polls > 0) {
          await Future.delayed(const Duration(seconds: 2));
        }
        polls += 1;
        final st = await _retryGet(Uri.parse('$url/job-status/$jobId'));
        if (st.statusCode == 404) {
          throw Exception('작업을 찾을 수 없습니다.');
        }
        if (st.statusCode != 200) {
          throw Exception(_apiError(st));
        }
        final js = jsonDecode(st.body) as Map<String, dynamic>;
        status = (js['status'] ?? 'processing').toString();
        final stage = (js['stage'] ?? '처리 중').toString();
        final percent = ((js['percent'] ?? 0) as num).toDouble().clamp(0, 100);
        if (!mounted) {
          return;
        }
        setState(() {
          _progress = percent / 100.0;
          _busyText = _stageLabel(stage);
        });
        if (status == 'failed') {
          throw Exception((js['error'] ?? '영상 제작에 실패했습니다.').toString());
        }
      }

      setState(() {
        _busyText = '다운로드 중...';
        _progress = 0.98;
      });
      final dl = await _retryGet(
        Uri.parse('$url/download/$jobId'),
        timeout: const Duration(seconds: 60),
      );
      if (dl.statusCode != 200) {
        throw Exception(_apiError(dl));
      }
      final dir = await getTemporaryDirectory();
      final out = File('${dir.path}/final_shorts_${DateTime.now().millisecondsSinceEpoch}.mp4');
      await out.writeAsBytes(dl.bodyBytes, flush: true);
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
      try {
        await Gal.putVideo(out.path);
        if (mounted) {
          _toast('갤러리에 저장했습니다.');
        }
      } catch (_) {}
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
                  const SizedBox(height: 18),
                  const _Label('제작 모드'),
                  SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: false, label: Text('⚡ 10초 초고속'), icon: Icon(Icons.bolt)),
                      ButtonSegment(value: true, label: Text('🎬 런웨이 AI'), icon: Icon(Icons.movie_filter_outlined)),
                    ],
                    selected: {_runwayMode},
                    onSelectionChanged: _busy
                        ? null
                        : (set) => setState(() => _runwayMode = set.first),
                    style: ButtonStyle(
                      backgroundColor: WidgetStateProperty.resolveWith((states) {
                        if (states.contains(WidgetState.selected)) {
                          return const Color(0xFFFF4D8D);
                        }
                        return const Color(0x331A1028);
                      }),
                    ),
                  ),
                  if (_runwayMode) ...[
                    const SizedBox(height: 12),
                    const _Label('카메라 모션'),
                    _OptionWrap(
                      options: const [
                        _Option('zoom_in', '줌인'),
                        _Option('drone', '드론 샷'),
                        _Option('pan', '패닝'),
                      ],
                      value: _cameraMotion,
                      onChanged: (v) => setState(() => _cameraMotion = v),
                    ),
                  ],
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
                  if (_media.isNotEmpty) ...[
                    const SizedBox(height: 20),
                    const _Label('AI 추천 스타일'),
                    if (_analyzing)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: Text(
                          '사진을 읽고 스타일을 추천하는 중...',
                          style: TextStyle(color: Colors.white.withValues(alpha: 0.55), fontSize: 13),
                        ),
                      ),
                    if (_aiStyles.isNotEmpty)
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: _aiStyles
                            .map(
                              (item) => ActionChip(
                                label: Text(item['label'] ?? ''),
                                onPressed: () => setState(() => _styleCtrl.text = item['prompt'] ?? ''),
                                backgroundColor: const Color(0x55FF4D8D),
                                side: const BorderSide(color: Color(0xFFFF4D8D)),
                              ),
                            )
                            .toList(),
                      ),
                  ],
                  const SizedBox(height: 20),
                  const _Label('2. 스타일 직접 입력'),
                  TextField(
                    controller: _styleCtrl,
                    minLines: 2,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      hintText: "예: 무한도전 스타일, 감성 브이로그, 뉴스 브리핑",
                    ),
                  ),
                  const SizedBox(height: 18),
                  _DropdownField(
                    label: '3. 목소리',
                    value: _voiceType,
                    options: const [
                      _Option('variety_male', '예능 남성'),
                      _Option('variety_female', '예능 여성'),
                      _Option('vlog_female', '브이로그 여성'),
                      _Option('fast_story_male', '빠른 스토리 남성'),
                      _Option('docu_male', '다큐 남성'),
                      _Option('radio_female', '라디오 여성'),
                      _Option('news_male', '뉴스 남성'),
                      _Option('news_female', '뉴스 여성'),
                    ],
                    onChanged: (v) => setState(() => _voiceType = v),
                  ),
                  const SizedBox(height: 14),
                  _DropdownField(
                    label: '4. BGM',
                    value: _bgmMood,
                    options: const [
                      _Option('variety', '예능'),
                      _Option('lofi', '로파이'),
                      _Option('phonk', '폰크'),
                      _Option('pop', '팝'),
                      _Option('acoustic', '어쿠스틱'),
                      _Option('suspense', '서스펜스'),
                      _Option('cinematic', '시네마틱'),
                      _Option('none', '음악 없음'),
                    ],
                    onChanged: (v) => setState(() => _bgmMood = v),
                  ),
                  const SizedBox(height: 14),
                  const _Label('5. 영상 속도'),
                  _OptionWrap(
                    options: const [
                      _Option('1.0', '1.0x 보통'),
                      _Option('1.2', '1.2x 숏폼 추천'),
                      _Option('1.5', '1.5x 빠른 템포'),
                    ],
                    value: _speed,
                    onChanged: (v) => setState(() => _speed = v),
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
          if (_busy) _LoadingMask(text: _busyText, progress: _progress),
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

class _DropdownField extends StatelessWidget {
  const _DropdownField({
    required this.label,
    required this.value,
    required this.options,
    required this.onChanged,
  });

  final String label;
  final String value;
  final List<_Option> options;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final current = options.any((o) => o.id == value) ? value : options.first.id;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _Label(label),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          decoration: BoxDecoration(
            color: const Color(0x331A1028),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: current,
              isExpanded: true,
              dropdownColor: const Color(0xFF1A1028),
              icon: const Icon(Icons.keyboard_arrow_down_rounded, color: Color(0xFFFFD3EA)),
              items: options
                  .map(
                    (option) => DropdownMenuItem(
                      value: option.id,
                      child: Text(option.label),
                    ),
                  )
                  .toList(),
              onChanged: (next) {
                if (next != null) {
                  onChanged(next);
                }
              },
            ),
          ),
        ),
      ],
    );
  }
}

class _Option {
  const _Option(this.id, this.label);
  final String id;
  final String label;
}

class _OptionWrap extends StatelessWidget {
  const _OptionWrap({
    required this.options,
    required this.value,
    required this.onChanged,
  });

  final List<_Option> options;
  final String value;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: options.map((option) {
        final selected = option.id == value;
        return ChoiceChip(
          label: Text(option.label),
          selected: selected,
          onSelected: (_) => onChanged(option.id),
          selectedColor: const Color(0xFFFF4D8D),
          backgroundColor: const Color(0x331A1028),
          labelStyle: TextStyle(
            color: selected ? Colors.white : Colors.white.withValues(alpha: 0.82),
            fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
          ),
          side: BorderSide(
            color: selected ? const Color(0xFFFF4D8D) : Colors.white.withValues(alpha: 0.12),
          ),
          showCheckmark: false,
        );
      }).toList(),
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
  const _LoadingMask({required this.text, required this.progress});
  final String text;
  final double progress;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: Colors.black.withValues(alpha: 0.62),
      child: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 36),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const CircularProgressIndicator(color: Color(0xFFFF4D8D)),
              const SizedBox(height: 18),
              Text(
                text,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, height: 1.4),
              ),
              const SizedBox(height: 16),
              ClipRRect(
                borderRadius: BorderRadius.circular(99),
                child: LinearProgressIndicator(
                  value: progress.clamp(0.02, 1.0),
                  minHeight: 8,
                  backgroundColor: Colors.white24,
                  color: const Color(0xFFFF4D8D),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                '${(progress.clamp(0, 1) * 100).round()}%',
                style: TextStyle(color: Colors.white.withValues(alpha: 0.7)),
              ),
            ],
          ),
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
