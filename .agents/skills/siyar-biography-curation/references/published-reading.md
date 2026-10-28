# تصحيح طبقة قراءة منشورة بطلب صريح

استخدم هذا المسار عند طلب المستخدم تحديث تشكيل أو ترتيب تراجم منشورة محددة. لا يفتح استدعاء دفعة جديدة هذا المسار ضمنيًا، ولا يستخدم لتغيير موضوعات أو هويات أو قرارات بشرية.

```powershell
python -m backend.reading_revision export --start N --end M
python -m backend.reading_revision status --batch <directory>
python -m backend.reading_revision validate --batch <directory> --authoring-only
python -m backend.reading_revision validate --batch <directory>
python -m backend.reading_revision preview --batch <directory>
python -m backend.reading_revision publish --batch <directory>
```

الأرقام رتب المصدر الشاملة، والحد الأقصى خمس تراجم للتصحيح الواحد. تقبل الأوامر `--db` و`--source`، والتصدير `--work-dir`، والمعاينة `--output`. استخدم مسار التصدير المعاد؛ يشمل معرّفه بصمة لقطة طبقات القراءة الحالية. احفظ التأليف عند الاستئناف. إذا تغير الأصل أو الطبقة أثناء العمل، توقف التحقق ويتطلب تصديرًا يطابق الحالة الجديدة؛ لا تمحُ قرارًا أحدث لتجاوز التعارض.

`manifest.json` بإصدار `reading-revision-v1` يحمل `curator` الحقيقي و`reason` المحدد. ملفات كل ترجمة: `source.json` لقطة ثابتة تضم `full_text,pilot,pilot_row,reading_row,diacritics_row`، ثم `reading.json` و`diacritics.json` للتأليف. لا تعدّل اللقطة. يحفظ مسار القراءة الموضوعات وسجلاتها كما هي، ويستعمل حدود النص الأصلية نفسها.

اتبع التشكيل الكامل والمراجعة السياقية المعتادين. يحمل `reading` الفقرات وتعليلاتها والتحقيق، ويمكنه تشكيل عناوين تنظيمية موجودة عبر `heading_labels:[{at,label}]`؛ موضع `at` بعد النقل الاختياري بـ`heading_overrides`. هذا الحقل يضيف الحركات فقط إلى ألفاظ العنوان السابق، ولا يستبدل عبارته خلسة. لا تحذف الأقوال أو الأسانيد أو نطاقات النقد لتحسين المظهر.

يحمل `audit.json` بصمة `authoring_sha256` التي يعيدها `status`، واسم القارئ المستقل المختلف عن `curator`، و`all_words_checked,all_paragraphs_checked`، وملاحظات `findings:[{note,resolved}]`. الفحوص المطلوبة: `source_coverage,paragraphs,criticism_context,negative_cases,diacritics,paragraph_context`. لا توقع قبل القراءة الفعلية. لا يصير التصحيح اعتمادًا بشريًا.

تُنشأ المعاينة في SQLite مستقلة، وتستخدم API8001 وواجهة5174 وفق الأوامر المعادة. `qa.json` مرتبط بـ`revision` و`content_sha256` من `preview.json`، مع `reviewer,checks,screenshots` الفعلية وفق بوابات الجودة، ومنها `diacritics_toggle`. أي تعديل للتأليف يبطل التدقيق والمعاينة وQA المرتبطة به.

بعد نجاح البوابات، يحفظ النشر نسخة SQLite احتياطية، ويؤرشف الصفين السابقين كاملين في `reading_revision_history`، ثم يعدّل `pilot_reading_overlays,curation_diacritics` في معاملة واحدة. تبقى النصوص والموضوعات والمراجعات ونسخ دفعات الفهرسة كما كانت. إعادة الإصدار نفسه تعيد `unchanged`؛ التصحيح اللاحق يبدأ من لقطة جديدة ويحفظ التسلسل.

تحقق بعد النشر بأداة مستقلة:

```powershell
python scripts/audit_reading_revision.py --db <database> --baseline <publisher-backup.sqlite3> --biography-ids <actual-ids> --source <source.mARkdown> --output <directory/integrity.json>
```

تثبت الأداة حفظ الجداول المحمية، وأن التغيير محصور في طبقات القراءة للأعلام المحددين، وأن الصفوف السابقة محفوظة كاملة في التاريخ. افحص القارئ بعد النشر ووضع الأصل ومواضع خارج النطاق، وسلّم البصمات والعدادات والالتباس والصور؛ لا تخلط تاريخ طبقة القراءة بإصدار فهرسة الموضوعات.
