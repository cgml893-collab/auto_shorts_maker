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
const kMasterProKey = 'MASTER-PRO-7777';

String compactLicense(String key) {
  return key.toUpperCase().replaceAll(RegExp(r'[^A-Z0-9]'), '');
}

bool isMasterProKey(String key) => compactLicense(key) == compactLicense(kMasterProKey);

String canonicalLicenseKey(String key) {
  final trimmed = key.trim();
  if (isMasterProKey(trimmed)) {
    return kMasterProKey;
  }
  return compactLicense(trimmed);
}
const _kJobUrl = 'active_job_url';
const _kLicenseKey = 'license_key';
const _kDeviceId = 'device_id';
const _kPlatform = 'platform';
const _kServerUrl = 'server_url';
const _kPlan = 'plan';

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
      title: 'ClipSpark AI',
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
      home: const StudioScreen(),
    );
  }
}

class StudioScreen extends StatefulWidget {
  const StudioScreen({super.key});

  @override
  State<StudioScreen> createState() => _StudioScreenState();
}

class _StudioScreenState extends State<StudioScreen> with WidgetsBindingObserver {
  final _styleCtrl = TextEditingController(text: '신나는 브이로그');
  final _picker = ImagePicker();
  List<XFile> _media = [];
  bool _busy = false;
  String _busyText = '릴스를 만들고 있어요...';
  double _progress = 0;
  String _voiceType = 'vlog_female';
  String _speed = '1.0';
  String _bgmMood = 'pop';
  List<Map<String, String>> _aiStyles = [];
  bool _analyzing = false;
  bool _sparkCinema = false;
  String _cameraMotion = 'zoom_in';
  File? _resultVideo;
  VideoPlayerController? _player;
  String _plan = 'free';
  String _statusBar = '[무료 체험: 1회 가능]';
  int _freeRemaining = 1;
  String _deviceId = '';
  String _platform = '';
  String _licenseKey = '';
  bool _resuming = false;

  List<String> get _allowedVoices {
    if (_plan == 'pro') {
      return const [
        'variety_male',
        'variety_female',
        'vlog_female',
        'fast_story_male',
        'docu_male',
        'radio_female',
        'news_male',
        'news_female',
      ];
    }
    if (_plan == 'basic') {
      return const ['variety_male', 'variety_female', 'vlog_female', 'fast_story_male'];
    }
    return const ['vlog_female', 'variety_male'];
  }

  List<String> get _allowedBgm {
    if (_plan == 'pro') {
      return const ['variety', 'lofi', 'phonk', 'pop', 'acoustic', 'suspense', 'cinematic', 'none'];
    }
    if (_plan == 'basic') {
      return const ['variety', 'lofi', 'pop', 'acoustic'];
    }
    return const ['pop', 'lofi'];
  }

  List<String> get _allowedSpeeds {
    if (_plan == 'pro') {
      return const ['1.0', '1.2', '1.5'];
    }
    return const ['1.0', '1.2'];
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _boot();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _styleCtrl.dispose();
    _player?.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _resumeActiveJob();
    }
  }

  Future<void> _boot() async {
    await _ensureDevice();
    await _refreshLicense();
    await _resumeActiveJob();
  }

  Future<void> _ensureDevice() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_kDeviceId) ?? '';
    var plat = prefs.getString(_kPlatform) ?? '';
    if (id.isEmpty) {
      final info = DeviceInfoPlugin();
      if (Platform.isAndroid) {
        id = (await info.androidInfo).id;
        plat = 'android';
      } else if (Platform.isIOS) {
        id = (await info.iosInfo).identifierForVendor ?? '';
        plat = 'ios';
      }
      await prefs.setString(_kDeviceId, id);
      await prefs.setString(_kPlatform, plat);
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _deviceId = id;
      _platform = plat;
      _licenseKey = prefs.getString(_kLicenseKey) ?? '';
      _plan = prefs.getString(_kPlan) ?? 'free';
    });
  }

  Future<Map<String, String>> _apiAuth() async {
    final prefs = await SharedPreferences.getInstance();
    var url = (prefs.getString(_kServerUrl) ?? kApiBaseUrl).replaceAll(RegExp(r'/$'), '');
    if (url.isEmpty || url.contains('192.168.') || url.contains('localhost')) {
      url = kApiBaseUrl;
    }
    return {
      'url': url,
      'license_key': prefs.getString(_kLicenseKey) ?? '',
      'device_id': prefs.getString(_kDeviceId) ?? _deviceId,
      'platform': prefs.getString(_kPlatform) ?? _platform,
    };
  }

  Future<void> _refreshLicense() async {
    final prefs = await SharedPreferences.getInstance();
    final storedKey = prefs.getString(_kLicenseKey) ?? '';
    if (isMasterProKey(storedKey)) {
      await prefs.setString(_kLicenseKey, kMasterProKey);
      await prefs.setString(_kPlan, 'pro');
      if (mounted) {
        setState(() {
          _licenseKey = kMasterProKey;
          _plan = 'pro';
          _statusBar = '[프로 VIP 회원]';
          _freeRemaining = 0;
        });
      }
    }
    try {
      final auth = await _apiAuth();
      final res = await http
          .post(
            Uri.parse('${auth['url']}/license-status'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({
              'device_id': auth['device_id'],
              'platform': auth['platform'],
              'license_key': auth['license_key'],
            }),
          )
          .timeout(const Duration(seconds: 20));
      if (res.statusCode != 200) {
        return;
      }
      final body = jsonDecode(res.body) as Map<String, dynamic>;
      final prefs = await SharedPreferences.getInstance();
      final plan = '${body['plan'] ?? 'free'}';
      await prefs.setString(_kPlan, plan);
      if (!mounted) {
        return;
      }
      setState(() {
        _plan = plan;
        _statusBar = '${body['status_bar'] ?? _defaultBar(plan)}';
        _freeRemaining = ((body['free_remaining'] ?? 0) as num).toInt();
        _licenseKey = auth['license_key'] ?? '';
        if (!_allowedVoices.contains(_voiceType)) {
          _voiceType = _allowedVoices.first;
        }
        if (!_allowedBgm.contains(_bgmMood)) {
          _bgmMood = _allowedBgm.first;
        }
        if (!_allowedSpeeds.contains(_speed)) {
          _speed = _allowedSpeeds.first;
        }
        if (_plan != 'pro') {
          _sparkCinema = false;
        }
      });
    } catch (_) {}
  }

  String _defaultBar(String plan) {
    if (plan == 'pro') {
      return '[프로 VIP 회원]';
    }
    if (plan == 'basic') {
      return '[베이직 회원]';
    }
    return '[무료 체험: 1회 가능]';
  }

  Future<void> _resumeActiveJob() async {
    if (_busy || _resuming) {
      return;
    }
    final prefs = await SharedPreferences.getInstance();
    final jobId = prefs.getString(_kJobId) ?? '';
    final url = prefs.getString(_kJobUrl) ?? '';
    if (jobId.isEmpty || url.isEmpty) {
      return;
    }
    _resuming = true;
    setState(() {
      _busy = true;
      _busyText = '백그라운드 작업 상태 확인 중...';
      _progress = 0.05;
    });
    try {
      await _pollAndDownload(url, jobId);
    } catch (_) {
      if (mounted) {
        setState(() => _busy = false);
      }
    } finally {
      _resuming = false;
    }
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
    if (stage.contains('스파크')) {
      return stage;
    }
    if (stage.contains('대본')) {
      return '대본 생성 중';
    }
    if (stage.contains('음성') || stage.contains('Eleven')) {
      return '음성 생성 중';
    }
    if (stage.contains('EXIF') || stage.contains('리사이즈') || stage.contains('프레임')) {
      return '사진 보정 중';
    }
    if (stage.contains('렌더') || stage.contains('합성') || stage.contains('영상') || stage.contains('패스')) {
      return '영상 합성 중';
    }
    if (stage.contains('완료')) {
      return '완료';
    }
    return stage;
  }

  bool _isPayment(http.Response res) {
    if (res.statusCode == 402) {
      return true;
    }
    try {
      final decoded = jsonDecode(res.body);
      if (decoded is Map && '${decoded['error']}' == 'PAYMENT_REQUIRED') {
        return true;
      }
    } catch (_) {}
    return false;
  }

  Future<void> _showPaymentModal([String? message]) async {
    if (!mounted) {
      return;
    }
    await showDialog<void>(
      context: context,
      builder: (ctx) {
        return AlertDialog(
          backgroundColor: const Color(0xFF1A1028),
          title: const Text('라이선스 구매가 필요합니다'),
          content: Text(
            message ?? '무료 체험이 만료되었습니다. 라이선스를 구매해 주세요.',
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('닫기')),
            FilledButton(
              onPressed: () {
                Navigator.pop(ctx);
                _openLicenseModal();
              },
              child: const Text('라이선스 키 입력'),
            ),
          ],
        );
      },
    );
  }

  Future<void> _openLicenseModal() async {
    final keyCtrl = TextEditingController(text: _licenseKey);
    final urlCtrl = TextEditingController(text: (await _apiAuth())['url']);
    if (!mounted) {
      return;
    }
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF140C24),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (ctx) {
        var busy = false;
        String? error;
        return StatefulBuilder(
          builder: (ctx, setModal) {
            return Padding(
              padding: EdgeInsets.fromLTRB(20, 18, 20, 18 + MediaQuery.of(ctx).viewInsets.bottom),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('라이선스 등록', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 8),
                  Text(
                    '베이직: 쾌속 무제한 · 성우 4종 · BGM 4종\n프로 VIP: ✨ 스파크 시네마 AI · 성우 8종 · 1080p',
                    style: TextStyle(color: Colors.white.withValues(alpha: 0.7), height: 1.4),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: urlCtrl,
                    decoration: const InputDecoration(hintText: '서버 주소'),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: keyCtrl,
                    obscureText: true,
                    decoration: const InputDecoration(hintText: 'ASM-PRO-XXXX 또는 MASTER-PRO-7777'),
                  ),
                  if (error != null) ...[
                    const SizedBox(height: 10),
                    Text(error!, style: const TextStyle(color: Color(0xFFFF8AA8))),
                  ],
                  const SizedBox(height: 16),
                  _PrimaryButton(
                    label: busy ? '인증 중...' : '라이선스 등록',
                    onPressed: busy
                        ? null
                        : () async {
                            setModal(() => busy = true);
                            try {
                              final url = urlCtrl.text.trim().replaceAll(RegExp(r'/$'), '');
                              final rawKey = keyCtrl.text.trim();
                              final key = isMasterProKey(rawKey) ? kMasterProKey : rawKey;
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
                              final masterPass = isMasterProKey(key);
                              if (res.statusCode != 200 && !masterPass) {
                                throw Exception(_apiError(res));
                              }
                              final prefs = await SharedPreferences.getInstance();
                              await prefs.setString(_kLicenseKey, key);
                              await prefs.setString(_kServerUrl, url);
                              await prefs.setBool('licensed', true);
                              if (masterPass) {
                                await prefs.setString(_kPlan, 'pro');
                              }
                              if (ctx.mounted) {
                                Navigator.pop(ctx);
                              }
                              await _refreshLicense();
                              if (mounted) {
                                _toast('라이선스가 등록되었습니다.');
                              }
                            } catch (e) {
                              setModal(() {
                                busy = false;
                                error = e.toString().replaceFirst('Exception: ', '');
                              });
                            }
                          },
                  ),
                ],
              ),
            );
          },
        );
      },
    );
    keyCtrl.dispose();
    urlCtrl.dispose();
  }

  Future<void> _selectMode(bool spark) async {
    if (spark && _plan != 'pro') {
      await _showPaymentModal('✨ 스파크 시네마 AI는 프로 VIP 전용입니다.');
      return;
    }
    setState(() => _sparkCinema = spark);
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
    if (_plan == 'free' && _freeRemaining <= 0) {
      await _showPaymentModal();
      return;
    }
    if (_sparkCinema && _plan != 'pro') {
      await _showPaymentModal('✨ 스파크 시네마 AI는 프로 VIP 전용입니다.');
      return;
    }
    final auth = await _apiAuth();
    final url = auth['url']!;
    final key = auth['license_key']!;
    final deviceId = auth['device_id']!;
    final platform = auth['platform']!;
    if (url.isEmpty || deviceId.isEmpty) {
      _toast('기기 정보를 읽지 못했습니다. 앱을 다시 시작해 주세요.');
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
          req.fields['license_key'] = isMasterProKey(key) ? kMasterProKey : key;
          req.fields['device_id'] = deviceId;
          req.fields['platform'] = platform;
          req.fields['voice_type'] = _voiceType;
          req.fields['speed_multiplier'] = _speed;
          req.fields['bgm_type'] = _bgmMood;
          req.fields['bgm_mood'] = _bgmMood;
          req.fields['is_spark_cinema'] = _sparkCinema ? 'true' : 'false';
          req.fields['is_runway_mode'] = _sparkCinema ? 'true' : 'false';
          req.fields['camera_motion'] = _cameraMotion;
          req.fields['output_height'] = _plan == 'pro' ? '1080' : '720';
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
          if (_isPayment(created)) {
            throw _PaymentException(_apiError(created));
          }
          if (created.statusCode >= 400 && created.statusCode < 500 && created.statusCode != 429) {
            throw Exception(_apiError(created));
          }
          uploadErr = Exception(_apiError(created));
        } catch (e) {
          uploadErr = e;
          if (e is _PaymentException) {
            break;
          }
        }
        await Future.delayed(Duration(milliseconds: 500 * (i + 1)));
      }
      if (uploadErr is _PaymentException) {
        if (mounted) {
          setState(() => _busy = false);
        }
        await _showPaymentModal(uploadErr.message);
        return;
      }
      if (created.statusCode != 200) {
        throw uploadErr ?? Exception('작업 요청에 실패했습니다.');
      }
      final body = jsonDecode(created.body) as Map<String, dynamic>;
      final jobId = (body['job_id'] ?? '').toString();
      if (jobId.isEmpty) {
        throw Exception('job_id를 받지 못했습니다.');
      }
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_kJobId, jobId);
      await prefs.setString(_kJobUrl, url);
      await _pollAndDownload(url, jobId);
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

  Future<void> _pollAndDownload(String url, String jobId) async {
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
      final percent = ((js['progress'] ?? js['percent'] ?? 0) as num).toDouble().clamp(0, 100);
      if (!mounted) {
        return;
      }
      setState(() {
        _busy = true;
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
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_kJobId);
    await prefs.remove(_kJobUrl);
    HapticFeedback.heavyImpact();
    if (!mounted) {
      return;
    }
    setState(() {
      _resultVideo = out;
      _player = player;
      _busy = false;
      _progress = 1;
    });
    try {
      await Gal.putVideo(out.path);
    } catch (_) {}
    await _refreshLicense();
    if (!mounted) {
      return;
    }
    await showDialog<void>(
      context: context,
      builder: (ctx) {
        return AlertDialog(
          backgroundColor: const Color(0xFF1A1028),
          title: const Text('🔔 릴스 영상이 완성되었습니다!'),
          content: const Text('바로 재생하며 갤러리에 저장했습니다.'),
          actions: [
            FilledButton(onPressed: () => Navigator.pop(ctx), child: const Text('확인')),
          ],
        );
      },
    );
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
    final visibleVoices = [
      for (final o in const [
        _Option('variety_male', '예능 남성'),
        _Option('variety_female', '예능 여성'),
        _Option('vlog_female', '브이로그 여성'),
        _Option('fast_story_male', '빠른 스토리 남성'),
        _Option('docu_male', '다큐 남성'),
        _Option('radio_female', '라디오 여성'),
        _Option('news_male', '뉴스 남성'),
        _Option('news_female', '뉴스 여성'),
      ])
        if (_allowedVoices.contains(o.id)) o,
    ];

    final visibleBgm = [
      for (final o in const [
        _Option('variety', '예능'),
        _Option('lofi', '로파이'),
        _Option('phonk', '폰크'),
        _Option('pop', '팝'),
        _Option('acoustic', '어쿠스틱'),
        _Option('suspense', '서스펜스'),
        _Option('cinematic', '시네마틱'),
        _Option('none', '음악 없음'),
      ])
        if (_allowedBgm.contains(o.id)) o,
    ];

    final visibleSpeeds = [
      const _Option('1.0', '1.0x 보통'),
      const _Option('1.2', '1.2x 숏폼 추천'),
      if (_plan == 'pro') const _Option('1.5', '1.5x 빠른 템포'),
    ];

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
                    title: 'ClipSpark AI',
                    subtitle: '사진만 올려도 15~20초 완성형 스토리와 세로 릴스를 만듭니다.',
                  ),
                  const SizedBox(height: 16),
                  _MembershipBar(
                    label: _statusBar,
                    onRegister: _openLicenseModal,
                  ),
                  const SizedBox(height: 18),
                  const _Label('제작 모드'),
                  SegmentedButton<bool>(
                    segments: [
                      const ButtonSegment(
                        value: false,
                        label: Text('⚡ 10초 쾌속 모드'),
                        icon: Icon(Icons.bolt),
                      ),
                      ButtonSegment(
                        value: true,
                        label: Text(_plan == 'pro' ? '✨ 스파크 시네마 AI' : '✨ 스파크 시네마 AI (PRO 🔒)'),
                        icon: const Icon(Icons.auto_awesome),
                      ),
                    ],
                    selected: {_sparkCinema},
                    onSelectionChanged: _busy
                        ? null
                        : (set) => _selectMode(set.first),
                    style: ButtonStyle(
                      backgroundColor: WidgetStateProperty.resolveWith((states) {
                        if (states.contains(WidgetState.selected)) {
                          return const Color(0xFFFF4D8D);
                        }
                        return const Color(0x331A1028);
                      }),
                    ),
                  ),
                  if (_sparkCinema) ...[
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
                    const _Label('AI 비전 분석 · 추천 스타일'),
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
                      hintText: '예: 감성 브이로그, 시네마틱 하이라이트',
                    ),
                  ),
                  const SizedBox(height: 18),
                  _DropdownField(
                    label: '3. 목소리',
                    value: _voiceType,
                    options: visibleVoices,
                    onChanged: (v) => setState(() => _voiceType = v),
                  ),
                  const SizedBox(height: 14),
                  _DropdownField(
                    label: '4. BGM 분위기',
                    value: _bgmMood,
                    options: visibleBgm,
                    onChanged: (v) => setState(() => _bgmMood = v),
                  ),
                  const SizedBox(height: 14),
                  _DropdownField(
                    label: '5. 배속 조절',
                    value: _speed,
                    options: visibleSpeeds,
                    onChanged: (v) {
                      if (v == '1.5' && _plan != 'pro') {
                        _showPaymentModal('1.5배속은 프로 VIP 전용입니다.');
                        return;
                      }
                      setState(() => _speed = v);
                    },
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

class _PaymentException implements Exception {
  _PaymentException(this.message);
  final String message;
  @override
  String toString() => message;
}

class _MembershipBar extends StatelessWidget {
  const _MembershipBar({required this.label, required this.onRegister});
  final String label;
  final VoidCallback onRegister;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0x331A1028),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Text(label, style: const TextStyle(fontWeight: FontWeight.w800)),
          ),
          TextButton(
            onPressed: onRegister,
            child: const Text('라이선스 등록'),
          ),
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
    final pct = (progress.clamp(0, 1) * 100).round();
    return ColoredBox(
      color: Colors.black.withValues(alpha: 0.72),
      child: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 36),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                '$pct%',
                style: const TextStyle(
                  fontSize: 64,
                  fontWeight: FontWeight.w900,
                  color: Color(0xFFFF4D8D),
                  height: 1,
                ),
              ),
              const SizedBox(height: 14),
              Text(
                text,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, height: 1.4),
              ),
              const SizedBox(height: 18),
              ClipRRect(
                borderRadius: BorderRadius.circular(99),
                child: LinearProgressIndicator(
                  value: progress.clamp(0.02, 1.0),
                  minHeight: 12,
                  backgroundColor: Colors.white24,
                  color: const Color(0xFFFF4D8D),
                ),
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
    if (decoded is Map) {
      final message = decoded['message'];
      if (message is String && message.isNotEmpty) {
        return message;
      }
      final detail = decoded['detail'];
      if (detail is String) {
        return detail;
      }
      if (detail is List) {
        return detail.map((e) => e.toString()).join('\n');
      }
    }
  } catch (_) {}
  if (res.body.isNotEmpty) {
    return res.body;
  }
  return '서버 오류 (${res.statusCode})';
}
