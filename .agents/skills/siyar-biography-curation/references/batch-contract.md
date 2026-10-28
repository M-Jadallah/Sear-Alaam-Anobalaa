# عقد الدفعة وأوامرها المحلية

نفّذ الأوامر من جذر مشروع الموسوعة. عند اختلاف واجهة الأداة عن هذا المرجع افحص `python -m backend.curation --help` والشيفرة الحالية، ولا تخمن أمر نشر بديلًا أو تستعمل ناشر التجربة القديمة.

## النطاق والاستئناف

```powershell
python -m backend.curation export --start N --end M --db <database> --source <source.mARkdown> --work-dir <work-root>
```

استبدل القيم بالقيم الفعلية؛ `N` و`M` حدان شاملان موجبان، وعدد التراجم `M-N+1` لا يتجاوز خمسًا. النطاق `source_ordinal` داخل المصدر المطابق لبصمة الملف. افتراضيًا تستخدم الأداة قاعدة المشروع ومصدره و`reference/curation` جذرًا للعمل.

التصدير يقرأ SQLite فقط، ويكتب مساحة العمل `<work-root>/<source_sha256>/<sha12>-<N>-<M>/`. أعد استخدام المسار الذي يعيده الأمر. وجود بيان سابق يعيد `resumed` ويحفظ التأليف. الترجمة المنشورة سابقًا تُعلّم `skipped` ولا تُستبدل؛ إذا كانت الدفعة كلها متجاوزة فلا تختلق ملفات لها أو تدعي تحريرها مجددًا.

`manifest.json` إصدار `curation-batch-v2`، ويثبت `batch_id` و`source_id` و`source_sha256` و`start_ordinal` و`end_ordinal` و`biographies`. لكل ترجمة: `id` و`source_ordinal` والعنوان وحدود المصدر و`status` و`directory` و`baseline_sha256`. لا تعدّل هذه الهوية أو البصمات لتجاوز تعارض. املأ `curator` بمعرّف صادق لوكيل التأليف المسؤول؛ يطابقه `reviewer` في ملفي التأليف. عند التعاون احتفظ في ملاحظات العمل بمن قرأ وما راجعه كل وكيل.

## ملفات كل ترجمة معلّقة

تبقى قراءة `curation-batch-v1` وبصماته السابقة متوافقة. للترقية الصريحة لدفعة قديمة غير منشورة فقط:

```powershell
python -m backend.curation upgrade --batch <batch-directory> --db <database> --source <source.mARkdown>
```

يحفظ الأمر نسخة من ملفات العمل، ويبقي النص والموضوعات والأسئلة المؤلفة، ويضيف ملف التشكيل وملاحظات الفقرات المطلوبة. يبطل التدقيق والمعاينة وQA السابقين؛ لا تُنقل علامات نجاحهما إلى الإصدار الجديد. يرفض ترقية المنشور أو استبداله، ولا يرقّي ملفات قديمة ضمنيًا عند مجرد الاستئناف.

تحت `biography-<source_ordinal>/`:

| الملف | العقد والعمل |
|---|---|
| `source.json` | أصل مصدّر للقراءة: `full_text` و`segments` و`existing_hits` و`footnote_candidates` وحدود الترجمة. لا يُحرّر. |
| `pilot.json` | طبقة العرض وسجل فحص كل المقاطع والوسوم السابقة ومقترحات الموضوعات، وفق التفصيل أدناه. |
| `reading.json` | إصدار `pilot-reading-v2`، و`paragraph_groups` و`criticism` و`heading_overrides` عند لزوم نقل عنوان مضاف. |
| `diacritics.json` | ملف إلزامي في الدفعات الجديدة لمراجعة كل الكلمات وإضافات الحركات فقط، وفق [مرجع التشكيل](diacritization.md). |
| `footnotes.json` | قرارات المرشحات وموقف متون الحواشي وتنبيه العرض، وفق [مرجع الحواشي](footnotes.md). |
| `gold.json` | من سؤال وصفي واحد إلى ثلاثة، لكل منها `question,start,end` وشاهد حقيقي مختار من القراءة قبل اختبار ترتيب البحث؛ أو `{items:[],unavailable_reason:"تعليل محدد"}` إن لم توجد مادة حقيقية لسؤال. |
| `audit.json` | تدقيق مستقل مرتبط ببصمة ملفات التأليف، لا اعتماد بشري. |

ملفات التأليف `pilot,reading,diacritics` تحمل `biography_id,source_sha256,source_char_start,source_char_end,reviewer` من التصدير. جميع النطاقات مطلقة بوحدات Unicode، ونهايتها غير شاملة.

### `pilot.json`

`segments` يحوي كل `segment_id` مرة واحدة: `assessment` من `supported,no_clear_topic,uncertain`، و`note`، و`topics`، و`existing_hits`. حالة `unexamined` المصدرة يجب أن تُستبدل **بعد القراءة**. توصية المرشح السابق `{hit_id,decision,reason}` و`decision` من `keep,modify,exclude,uncertain`؛ لا تضف حقول اعتماد بشري.

كل موضوع يحوي `topic,category,aliases,rationale,relation_type,subject_scope,stance,evidence_start,evidence_end,context_start,context_end`. الشاهد داخل المقطع والسياق داخل الترجمة. أسماء القيم ودلالاتها في [مرجع الموضوعات](topic-review.md)؛ الباب يجب أن يكون موجودًا في الدليل، أما التسمية الجديدة فتبقى مقترحة.

`presentation` يحوي:

- `headings:[{at,label}]` للعناوين المضافة.
- `quotes,poetry,names,keywords,bold,footnotes` قوائم `{start,end}`. `footnotes` يساوي مواضع الإشارات المثبتة المنقولة فقط.
- `lists:[{start,end,ordered,items:[{start,end}]}]`، مع عناصر غير متداخلة وبترتيب المصدر.

### `reading.json`

`paragraph_groups:[{start,end}]` يقسم المصدر كاملًا، بما فيه عنوانه؛ لا تفصل زخرفة `quotes` الفقرات. `criticism:[{start,end,context_start,context_end,kind,reason}]` مرتب غير متداخل، و`kind` من `hadith,isnad,narrator,report`. `heading_overrides:[{from_at,at}]` ينقل عنوانًا مضافًا محددًا موجودًا فقط، عند الحاجة؛ لا تنسخ مواضع العينة القديمة إلى دفعة جديدة.

إضافةً إلى `paragraph_groups`، يحمل `reading.json` في الدفعة الجديدة `paragraph_notes:[{start,end,context_start,context_end,reason}]`، بسجل واحد لكل مجموعة. النطاقان الأولان يطابقان المجموعة، والسياق يحتويها داخل الترجمة، وسبب الفصل أو الوصل محدد من القراءة. لا تنسخ سببًا عامًا لكل الفقرات، ولا تجعل رقم صفحة أو اقتباس حدًا قسريًا.

### `footnotes.json`

`candidates:[{start,end,raw,decision,reason}]` يراجع جميع المرشحات المصدرة مرة واحدة، بالنص الحرفي نفسه؛ `decision` من `verified_marker,body_literal,ambiguous`. النشر الحالي ينقل فقط الإشارة الرقمية ASCII المثبتة مثل `(1)`؛ غيرها يبقى ظاهرًا ويُسجل القيد، ولا يُحذف خلسة.

احفظ `bodies_status` من `absent,present_unlinked,uncertain`، و`body_ranges:[{start,end}]`، و`notice` صادقًا للدفعة؛ انسخ التنبيه نفسه حرفيًا إلى `pilot.presentation.footnote_notice`. `present_unlinked` يحتاج نطاق متن واحدًا على الأقل، و`absent` لا تحمل نطاقات متن. الإصدار الحالي يرفض `linked` لأن الربط الفعلي بين الإشارة والمتن غير مدعوم؛ تبقى المتون المكتشفة ظاهرة بوصفها `present_unlinked`، ولا تُكتب رسالة الغياب. قد لا يغطي كاشف الأقواس القصيرة كل شكل جديد؛ افحص النص كاملًا وأبق غير المدعوم ظاهرًا مع ملاحظة، ولا تعدل `source.json` لإجباره على القبول.

### `audit.json`

يعيد `status` بصمة `authoring_sha256` لكل ترجمة؛ تشمل المصدر المصدّر و`pilot,reading,footnotes,gold`، وليست مجرد بصمة بايتات ملف واحد. بعد قراءة مستقلة للأصل والتحرير، يضع المدقق اسمه المختلف عن `manifest.curator` والبصمة الحالية، ويُسجل ملاحظاته في `findings:[{note,resolved}]`. القيم `all_segments_checked` و`all_footnotes_checked` و`checks.source_coverage,paragraphs,criticism_context,topic_evidence,negative_cases` لا تُضبط `true` إلا بنتيجة الفحص الفعلي. وجود ملاحظة غير محلولة يمنع النشر.

## التحقق والمعاينة والنشر

تضم بصمة التأليف في v2 ملف `diacritics.json` أيضًا، وتلزم بوابتا التدقيق `checks.diacritics` و`checks.paragraph_context`، إضافةً إلى البوابات السابقة. لا يُرحّل نجاح v1 إلى هذه البوابات؛ المدقق يراجع الضبط والإعراب والأسماء وأسباب الحدود فعليًا. الحالات اللغوية المعلقة الموثقة ليست إخفاق تحقق بنيويًا، لكنها تبقى ظاهرة بوصفها غير محسومة.

```powershell
python -m backend.curation status --batch <batch-directory> --db <database> --source <source.mARkdown>
python -m backend.curation validate --batch <batch-directory> --db <database> --source <source.mARkdown> --authoring-only
python -m backend.curation validate --batch <batch-directory> --db <database> --source <source.mARkdown>
python -m backend.curation preview --batch <batch-directory> --db <database> --source <source.mARkdown>
python -m backend.curation publish --batch <batch-directory> --db <database> --source <source.mARkdown>
```

`--authoring-only` لفحص التأليف قبل التدقيق المستقل، ولا يسمح بالنشر. `validate` الكامل لا ينشر. يوضح `status` مرحلة الدفعة و`gates` و`remaining` وأسباب النقص؛ استأنف المرحلة الناقصة ولا تصفر عملًا صالحًا. `preview` يتطلب اكتمال التأليف والتدقيق ويصنع نسخة SQLite معزولة، ويكتب `preview.json` مرتبطًا بإصدار الدفعة وبصمة `gold_frozen_sha256`، ويعيد أوامر تشغيلها في خادم محلي مستقل، وأوامر واجهة مستقلة تستخدم `VITE_API_TARGET` لهدف المعاينة. وجه الواجهة إلى ذلك الخادم أثناء QA؛ لا تختبر قرارات المراجعة على القاعدة الحقيقية.

إذا تغيرت الملفات بعد المعاينة، أعد التدقيق ثم أنشئ معاينة جديدة عبر `--output <new-preview.sqlite3>`؛ الأداة لا تستبدل معاينة موجودة. لا تنقل `qa.json` أو البصمات من الإصدار السابق. املأ QA من [التحقق الفعلي](quality-gates.md)، ثم انشر إذا كان النشر المحلي ضمن طلب المستخدم. النشر يأخذ نسخة SQLite متسقة وينفذ معاملة ذرية إضافةً إلى السابق؛ يعيد الإصدار نفسه `unchanged`، ويرفض استبدال دفعة منشورة بإصدار مختلف.

المنشور محليًا يظهر عبر `GET /api/curation/batches` وفي القارئ والمراجعة والبحث؛ ذلك لا يحوّل مقترحات الوكلاء إلى قرارات بشرية. إذا كان النطاق غير منشور فلا تتوقع أن يعكس التطبيق تعديلات مساحة العمل قبل المعاينة أو النشر.
