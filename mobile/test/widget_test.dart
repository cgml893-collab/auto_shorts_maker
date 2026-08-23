import 'package:flutter_test/flutter_test.dart';

import 'package:auto_shorts_mobile/main.dart';

void main() {
  testWidgets('ClipSpark studio loads', (WidgetTester tester) async {
    await tester.pumpWidget(const AutoShortsApp());
    expect(find.text('ClipSpark AI'), findsOneWidget);
  });
}
