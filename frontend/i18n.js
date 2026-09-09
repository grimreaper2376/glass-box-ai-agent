/* GlassBox — whole-page translation.
 *
 * Rather than tagging each element, this keys on the *English source text* of
 * every text node and translatable attribute, remembers each node's original,
 * and swaps it for the active language. A MutationObserver re-runs on any DOM
 * the app renders later, so panels built after load are translated too. Strings
 * not in the dictionary (live prices, tickers, coin names, the analyst's own
 * generated prose) are left exactly as they are — only known UI copy changes.
 */
(function () {
  "use strict";

  var LANGS = ["zh", "es", "hi", "ar", "pt", "fr", "ru"];
  window.LANG_NAME = { en: "English", zh: "Chinese", es: "Spanish", hi: "Hindi",
                       ar: "Arabic", pt: "Portuguese", fr: "French", ru: "Russian" };
  window.currentLang = "en";

  // English source -> [zh, es, hi, ar, pt, fr, ru]
  var TR = {
    // --- sidebar / nav / chrome ---
    "Accountable agent trading": ["可问责的智能体交易", "Trading de agentes con rendición de cuentas", "जवाबदेह एजेंट ट्रेडिंग", "تداول وكيل مسؤول", "Trading de agente responsável", "Trading d'agent responsable", "Подотчётная торговля агента"],
    "Cockpit": ["驾驶舱", "Cabina", "कॉकपिट", "لوحة القيادة", "Cabine", "Cockpit", "Пульт"],
    "Control Center": ["控制中心", "Centro de control", "नियंत्रण केंद्र", "مركز التحكم", "Centro de controlo", "Centre de contrôle", "Центр управления"],
    "Payments": ["支付", "Pagos", "भुगतान", "المدفوعات", "Pagamentos", "Paiements", "Платежи"],
    "Onchain & Pay": ["链上与支付", "Onchain y pago", "ऑनचेन और भुगतान", "السلسلة والدفع", "Onchain e pagamento", "Onchain et paiement", "Ончейн и оплата"],
    "Coins": ["币种", "Monedas", "सिक्के", "العملات", "Moedas", "Cryptos", "Монеты"],
    "Pairs": ["交易对", "Pares", "जोड़े", "الأزواج", "Pares", "Paires", "Пары"],
    "Events": ["事件", "Eventos", "घटनाएँ", "الأحداث", "Eventos", "Événements", "События"],
    "Council": ["议会", "Consejo", "परिषद", "المجلس", "Conselho", "Conseil", "Совет"],
    "Track record": ["历史记录", "Historial", "ट्रैक रिकॉर्ड", "السجل", "Histórico", "Historique", "История"],
    "Sentinel": ["哨兵", "Centinela", "प्रहरी", "الحارس", "Sentinela", "Sentinelle", "Страж"],
    "Narratives": ["叙事", "Narrativas", "आख्यान", "السرديات", "Narrativas", "Narratifs", "Нарративы"],
    "Yield": ["收益", "Rendimiento", "यील्ड", "العائد", "Rendimento", "Rendement", "Доходность"],
    "Ledger": ["账本", "Libro mayor", "बहीखाता", "دفتر الحسابات", "Livro-razão", "Registre", "Реестр"],
    "Backtest": ["回测", "Backtest", "बैकटेस्ट", "اختبار رجعي", "Backtest", "Backtest", "Бэктест"],
    "Constitution": ["章程", "Constitución", "संविधान", "الدستور", "Constituição", "Constitution", "Конституция"],
    "Stop all new risk": ["停止所有新风险", "Detener todo riesgo nuevo", "सभी नया जोखिम रोकें", "إيقاف كل مخاطرة جديدة", "Parar todo risco novo", "Arrêter tout nouveau risque", "Остановить весь новый риск"],
    "Dark": ["深色", "Oscuro", "गहरा", "داكن", "Escuro", "Sombre", "Тёмная"],
    "Light": ["浅色", "Claro", "हल्का", "فاتح", "Claro", "Clair", "Светлая"],
    "Language": ["语言", "Idioma", "भाषा", "اللغة", "Idioma", "Langue", "Язык"],

    // --- desk ---
    "Ask the desk": ["咨询分析台", "Pregunta al escritorio", "डेस्क से पूछें", "اسأل المكتب", "Pergunte à mesa", "Demander au bureau", "Спросить аналитика"],
    "Connect a model": ["连接模型", "Conectar un modelo", "मॉडल कनेक्ट करें", "وصل نموذج", "Conectar um modelo", "Connecter un modèle", "Подключить модель"],
    "Send": ["发送", "Enviar", "भेजें", "إرسال", "Enviar", "Envoyer", "Отправить"],
    "Ask about a coin or pair…": ["询问某个币种或交易对…", "Pregunta por una moneda o par…", "किसी सिक्के या जोड़े के बारे में पूछें…", "اسأل عن عملة أو زوج…", "Pergunte sobre uma moeda ou par…", "Posez une question sur une crypto ou une paire…", "Спросите о монете или паре…"],
    "Which brain is answering": ["由哪个大脑作答", "Qué cerebro responde", "कौन-सा दिमाग जवाब दे रहा है", "أي عقل يجيب", "Qual cérebro responde", "Quel cerveau répond", "Какой мозг отвечает"],
    "Paste your free API key": ["粘贴你的免费 API 密钥", "Pega tu clave API gratuita", "अपनी मुफ़्त API कुंजी पेस्ट करें", "الصق مفتاح API المجاني", "Cole a sua chave API gratuita", "Collez votre clé API gratuite", "Вставьте бесплатный API-ключ"],
    "Model": ["模型", "Modelo", "मॉडल", "النموذج", "Modelo", "Modèle", "Модель"],
    "Cancel": ["取消", "Cancelar", "रद्द करें", "إلغاء", "Cancelar", "Annuler", "Отмена"],
    "Disconnect": ["断开连接", "Desconectar", "डिस्कनेक्ट", "قطع الاتصال", "Desconectar", "Déconnecter", "Отключить"],

    // --- cockpit ---
    "Capital lifecycle": ["资金生命周期", "Ciclo de vida del capital", "पूंजी जीवनचक्र", "دورة حياة رأس المال", "Ciclo de vida do capital", "Cycle de vie du capital", "Жизненный цикл капитала"],
    "Equity": ["净值", "Patrimonio", "इक्विटी", "حقوق الملكية", "Património", "Capitaux propres", "Капитал"],
    "Session": ["会话", "Sesión", "सत्र", "الجلسة", "Sessão", "Session", "Сессия"],
    "Net P&L (whole account)": ["净盈亏（整个账户）", "P&L neto (cuenta completa)", "शुद्ध P&L (पूरा खाता)", "صافي الربح والخسارة (الحساب كامل)", "P&L líquido (conta inteira)", "P&L net (compte entier)", "Чистый P&L (весь счёт)"],
    "Drawdown": ["回撤", "Caída máxima", "ड्रॉडाउन", "التراجع", "Recuo", "Repli", "Просадка"],
    "Autonomous trading": ["自主交易", "Trading autónomo", "स्वायत्त ट्रेडिंग", "التداول الذاتي", "Trading autónomo", "Trading autonome", "Автономная торговля"],
    "Enable autonomous trading": ["启用自主交易", "Activar trading autónomo", "स्वायत्त ट्रेडिंग सक्षम करें", "تفعيل التداول الذاتي", "Ativar trading autónomo", "Activer le trading autonome", "Включить автономную торговлю"],
    "Place a trade": ["下单交易", "Realizar una operación", "एक ट्रेड करें", "نفّذ صفقة", "Fazer uma operação", "Passer une transaction", "Разместить сделку"],
    "Place trade": ["下单", "Realizar operación", "ट्रेड करें", "نفّذ الصفقة", "Fazer operação", "Passer l'ordre", "Разместить сделку"],
    "Spot": ["现货", "Spot", "स्पॉट", "فوري", "Spot", "Spot", "Спот"],
    "Futures": ["合约", "Futuros", "फ्यूचर्स", "العقود الآجلة", "Futuros", "Futures", "Фьючерсы"],
    "Buy": ["买入", "Comprar", "खरीदें", "شراء", "Comprar", "Acheter", "Купить"],
    "Sell": ["卖出", "Vender", "बेचें", "بيع", "Vender", "Vendre", "Продать"],
    "Open Long": ["开多", "Abrir largo", "लॉन्ग खोलें", "فتح شراء", "Abrir comprado", "Ouvrir long", "Открыть лонг"],
    "Open Short": ["开空", "Abrir corto", "शॉर्ट खोलें", "فتح بيع", "Abrir vendido", "Ouvrir short", "Открыть шорт"],
    "Leverage": ["杠杆", "Apalancamiento", "लीवरेज", "الرافعة", "Alavancagem", "Levier", "Плечо"],
    "Amount $": ["金额 $", "Importe $", "राशि $", "المبلغ $", "Montante $", "Montant $", "Сумма $"],
    "I understand this places real orders in my Agentic sub-account.": ["我了解这将在我的智能体子账户中下达真实订单。", "Entiendo que esto coloca órdenes reales en mi subcuenta Agentic.", "मैं समझता हूँ कि यह मेरे Agentic सब-अकाउंट में वास्तविक ऑर्डर देता है।", "أفهم أن هذا ينفّذ أوامر حقيقية في حسابي الفرعي Agentic.", "Compreendo que isto coloca ordens reais na minha subconta Agentic.", "Je comprends que cela passe de vrais ordres dans mon sous-compte Agentic.", "Я понимаю, что это размещает реальные ордера в моём субсчёте Agentic."],
    "Futures positions": ["合约持仓", "Posiciones de futuros", "फ्यूचर्स पोज़िशन", "مراكز العقود الآجلة", "Posições de futuros", "Positions futures", "Позиции по фьючерсам"],
    "USDⓈ-M perpetuals — leveraged, isolated margin. Green is long, red is short.": ["USDⓈ 本位永续——带杠杆、逐仓保证金。绿色为多头，红色为空头。", "Perpetuos USDⓈ-M — apalancados, margen aislado. Verde es largo, rojo es corto.", "USDⓈ-M perpetual — लीवरेज्ड, आइसोलेटेड मार्जिन। हरा लॉन्ग, लाल शॉर्ट।", "عقود USDⓈ-M الدائمة — برافعة وهامش معزول. الأخضر شراء والأحمر بيع.", "Perpétuos USDⓈ-M — alavancados, margem isolada. Verde é comprado, vermelho é vendido.", "Perpétuels USDⓈ-M — à effet de levier, marge isolée. Vert = long, rouge = short.", "Бессрочные USDⓈ-M — с плечом, изолированная маржа. Зелёный — лонг, красный — шорт."],
    "Open positions": ["未平仓头寸", "Posiciones abiertas", "खुली पोज़िशन", "المراكز المفتوحة", "Posições abertas", "Positions ouvertes", "Открытые позиции"],
    "Market": ["市场", "Mercado", "बाज़ार", "السوق", "Mercado", "Marché", "Рынок"],
    "Activity": ["活动", "Actividad", "गतिविधि", "النشاط", "Atividade", "Activité", "Активность"],
    "Recent decisions": ["近期决策", "Decisiones recientes", "हालिया निर्णय", "القرارات الأخيرة", "Decisões recentes", "Décisions récentes", "Недавние решения"],
    "Each one is a signed ledger entry": ["每一条都是已签名的账本记录", "Cada una es una entrada firmada del libro mayor", "प्रत्येक एक हस्ताक्षरित बहीखाता प्रविष्टि है", "كل واحدة إدخال موقّع في الدفتر", "Cada uma é uma entrada assinada no livro-razão", "Chacune est une entrée signée du registre", "Каждая — подписанная запись в реестре"],
    "Start engine": ["启动引擎", "Iniciar motor", "इंजन शुरू करें", "تشغيل المحرك", "Iniciar motor", "Démarrer le moteur", "Запустить движок"],

    // --- control center ---
    "Mode & data source": ["模式与数据源", "Modo y fuente de datos", "मोड और डेटा स्रोत", "الوضع ومصدر البيانات", "Modo e fonte de dados", "Mode et source de données", "Режим и источник данных"],
    "Use live Binance market data": ["使用币安实时行情数据", "Usar datos de mercado en vivo de Binance", "बायनेंस का लाइव मार्केट डेटा उपयोग करें", "استخدام بيانات سوق بينانس الحية", "Usar dados de mercado ao vivo da Binance", "Utiliser les données de marché en direct de Binance", "Использовать живые рыночные данные Binance"],
    "Read live from Binance for this session, not assumed in advance": ["本会话从币安实时读取，而非事先假定", "Se lee en vivo de Binance para esta sesión, no se asume de antemano", "इस सत्र के लिए बायनेंस से लाइव पढ़ा जाता है, पहले से मान नहीं लिया जाता", "يُقرأ حيًّا من بينانس لهذه الجلسة، وليس مفترضًا مسبقًا", "Lido ao vivo da Binance para esta sessão, não assumido antecipadamente", "Lu en direct depuis Binance pour cette session, non supposé à l'avance", "Читается вживую из Binance для этой сессии, не предполагается заранее"],
    "Live mode places real orders on Binance with real funds.": ["实盘模式将用真实资金在币安下达真实订单。", "El modo en vivo coloca órdenes reales en Binance con fondos reales.", "लाइव मोड बायनेंस पर असली फंड से असली ऑर्डर देता है।", "الوضع الحي ينفّذ أوامر حقيقية على بينانس بأموال حقيقية.", "O modo ao vivo coloca ordens reais na Binance com fundos reais.", "Le mode en direct passe de vrais ordres sur Binance avec de vrais fonds.", "Живой режим размещает реальные ордера на Binance с реальными средствами."],
    "Authorize with Binance": ["通过币安授权", "Autorizar con Binance", "बायनेंस से अधिकृत करें", "التفويض عبر بينانس", "Autorizar com a Binance", "Autoriser avec Binance", "Авторизоваться через Binance"],
    "Required once before Live mode can authorize. No shell required.": ["在实盘模式授权前需设置一次。无需命令行。", "Necesario una vez antes de que el modo en vivo pueda autorizar. Sin terminal.", "लाइव मोड के अधिकृत होने से पहले एक बार आवश्यक। कोई शेल नहीं।", "مطلوب مرة واحدة قبل أن يتمكن الوضع الحي من التفويض. لا حاجة لسطر الأوامر.", "Necessário uma vez antes de o modo ao vivo poder autorizar. Sem terminal.", "Requis une fois avant que le mode en direct puisse autoriser. Aucun terminal requis.", "Требуется один раз перед авторизацией живого режима. Терминал не нужен."],
    "Required once before this mode can reach Binance's real login.": ["在此模式访问币安真实登录前需设置一次。", "Necesario una vez antes de que este modo pueda acceder al inicio de sesión real de Binance.", "इस मोड के बायनेंस के असली लॉगिन तक पहुँचने से पहले एक बार आवश्यक।", "مطلوب مرة واحدة قبل أن يصل هذا الوضع إلى تسجيل دخول بينانس الحقيقي.", "Necessário uma vez antes de este modo alcançar o login real da Binance.", "Requis une fois avant que ce mode puisse atteindre la vraie connexion Binance.", "Требуется один раз перед доступом этого режима к реальному входу Binance."],
    "Run the safety drills": ["运行安全演练", "Ejecutar las pruebas de seguridad", "सुरक्षा ड्रिल चलाएँ", "تشغيل اختبارات السلامة", "Executar os testes de segurança", "Lancer les tests de sécurité", "Запустить проверки безопасности"],
    "Run 12 drills": ["运行 12 项演练", "Ejecutar 12 pruebas", "12 ड्रिल चलाएँ", "تشغيل 12 اختبارًا", "Executar 12 testes", "Lancer 12 tests", "Запустить 12 проверок"],
    "Goes through every rule the AI's own trades do — nothing here is a bypass": ["经过 AI 自身交易所遵循的每一条规则——这里没有任何绕过", "Pasa por cada regla que siguen las propias operaciones de la IA — nada aquí es un atajo", "AI के अपने ट्रेड जिन नियमों से गुज़रते हैं, सभी से गुज़रता है — यहाँ कुछ भी बायपास नहीं", "يمر بكل قاعدة تمر بها صفقات الذكاء الاصطناعي نفسها — لا شيء هنا التفاف", "Passa por todas as regras que as próprias operações da IA seguem — nada aqui é um atalho", "Passe par chaque règle que suivent les transactions de l'IA — rien ici n'est un contournement", "Проходит через каждое правило, что и сделки самого ИИ — здесь нет обхода"],
    "Run a simulated stress test": ["运行模拟压力测试", "Ejecutar una prueba de estrés simulada", "एक सिम्युलेटेड स्ट्रेस टेस्ट चलाएँ", "تشغيل اختبار ضغط محاكى", "Executar um teste de stress simulado", "Lancer un test de résistance simulé", "Запустить симуляцию стресс-теста"],
    "Simulated market only. Prove the safety story rather than describing it.": ["仅为模拟市场。用行动而非描述来证明安全性。", "Solo mercado simulado. Demuestra la seguridad en vez de describirla.", "केवल सिम्युलेटेड बाज़ार। सुरक्षा को बताने के बजाय सिद्ध करें।", "سوق محاكى فقط. أثبت السلامة بدل وصفها.", "Apenas mercado simulado. Prove a segurança em vez de descrevê-la.", "Marché simulé uniquement. Prouvez la sûreté plutôt que de la décrire.", "Только симулированный рынок. Докажите безопасность, а не описывайте её."],
    "Backfill the analyst track record": ["回填分析师历史记录", "Rellenar el historial de los analistas", "विश्लेषक ट्रैक रिकॉर्ड भरें", "تعبئة سجل المحللين", "Preencher o histórico dos analistas", "Remplir l'historique des analystes", "Заполнить историю аналитиков"],
    "Run calibration": ["运行校准", "Ejecutar calibración", "कैलिब्रेशन चलाएँ", "تشغيل المعايرة", "Executar calibração", "Lancer la calibration", "Запустить калибровку"],
    "Real Binance history — updates the Track Record tab immediately.": ["真实币安历史——立即更新历史记录标签页。", "Historial real de Binance — actualiza la pestaña Historial de inmediato.", "असली बायनेंस इतिहास — ट्रैक रिकॉर्ड टैब तुरंत अपडेट करता है।", "سجل بينانس حقيقي — يحدّث تبويب السجل فورًا.", "Histórico real da Binance — atualiza a aba Histórico imediatamente.", "Historique réel de Binance — met à jour l'onglet Historique immédiatement.", "Реальная история Binance — сразу обновляет вкладку Истории."],
    "What the agent is allowed to do right now": ["智能体当前被允许执行的操作", "Lo que el agente puede hacer ahora mismo", "एजेंट अभी क्या कर सकता है", "ما يُسمح للوكيل بفعله الآن", "O que o agente pode fazer agora", "Ce que l'agent est autorisé à faire maintenant", "Что агенту разрешено делать сейчас"],

    // --- payments / onchain ---
    "Settlement wallet": ["结算钱包", "Cartera de liquidación", "सेटलमेंट वॉलेट", "محفظة التسوية", "Carteira de liquidação", "Portefeuille de règlement", "Расчётный кошелёк"],
    "Inspect any address or transaction": ["检查任意地址或交易", "Inspecciona cualquier dirección o transacción", "किसी भी पते या लेन-देन की जाँच करें", "افحص أي عنوان أو معاملة", "Inspecione qualquer endereço ou transação", "Inspecter n'importe quelle adresse ou transaction", "Проверьте любой адрес или транзакцию"],
    "Look up": ["查询", "Buscar", "देखें", "بحث", "Procurar", "Rechercher", "Найти"],
    "Payment counterparties": ["支付交易对手", "Contrapartes de pago", "भुगतान प्रतिपक्ष", "أطراف الدفع", "Contrapartes de pagamento", "Contreparties de paiement", "Контрагенты платежей"],
    "Add to allowlist": ["加入白名单", "Añadir a la lista blanca", "अनुमति सूची में जोड़ें", "أضف إلى قائمة السماح", "Adicionar à lista de permissões", "Ajouter à la liste blanche", "Добавить в белый список"],
    "Payments made": ["已完成支付", "Pagos realizados", "किए गए भुगतान", "المدفوعات المنفذة", "Pagamentos efetuados", "Paiements effectués", "Совершённые платежи"],
    "Onchain & payment workflows": ["链上与支付流程", "Flujos onchain y de pago", "ऑनचेन और भुगतान वर्कफ़्लो", "سير عمل السلسلة والدفع", "Fluxos onchain e de pagamento", "Flux onchain et de paiement", "Ончейн и платёжные процессы"],
    "Paper wallet": ["模拟钱包", "Cartera simulada", "पेपर वॉलेट", "محفظة تجريبية", "Carteira de papel", "Portefeuille papier", "Бумажный кошелёк"],
    "Staked": ["已质押", "En staking", "स्टेक किया गया", "مربوط", "Em staking", "En staking", "В стейкинге"],
    "Staking": ["质押", "Staking", "स्टेकिंग", "الرهن", "Staking", "Staking", "Стейкинг"],
    "Stake paper USDC into an asset to earn yield; claim rewards anytime.": ["将模拟 USDC 质押到某资产以获取收益，可随时领取奖励。", "Deposita USDC simulados en un activo para generar rendimiento; reclama recompensas cuando quieras.", "यील्ड कमाने के लिए पेपर USDC को किसी एसेट में स्टेक करें; कभी भी रिवॉर्ड क्लेम करें।", "اربط USDC التجريبي في أصل لكسب عائد؛ طالب بالمكافآت في أي وقت.", "Faça staking de USDC de papel num ativo para gerar rendimento; reivindique recompensas a qualquer momento.", "Stakez des USDC papier dans un actif pour générer du rendement ; réclamez les récompenses à tout moment.", "Застейкайте бумажные USDC в актив для дохода; забирайте награды в любой момент."],
    "Stake": ["质押", "Depositar", "स्टेक", "ربط", "Depositar", "Staker", "Застейкать"],
    "Unstake": ["解押", "Retirar", "अनस्टेक", "فك", "Retirar", "Retirer", "Вывести"],
    "Claim": ["领取", "Reclamar", "क्लेम", "مطالبة", "Reivindicar", "Réclamer", "Забрать"],
    "Swap (DeFi)": ["兑换 (DeFi)", "Intercambio (DeFi)", "स्वैप (DeFi)", "مبادلة (DeFi)", "Troca (DeFi)", "Échange (DeFi)", "Обмен (DeFi)"],
    "Swap USDC into another asset at a reference rate.": ["按参考汇率将 USDC 兑换为另一种资产。", "Intercambia USDC por otro activo a un tipo de referencia.", "संदर्भ दर पर USDC को दूसरी एसेट में स्वैप करें।", "بدّل USDC إلى أصل آخر بسعر مرجعي.", "Troque USDC por outro ativo a uma taxa de referência.", "Échangez des USDC contre un autre actif à un taux de référence.", "Обменяйте USDC на другой актив по референсному курсу."],
    "Swap": ["兑换", "Intercambiar", "स्वैप", "مبادلة", "Trocar", "Échanger", "Обменять"],
    "Agent-to-agent payment": ["智能体间支付", "Pago entre agentes", "एजेंट-से-एजेंट भुगतान", "دفع بين الوكلاء", "Pagamento entre agentes", "Paiement entre agents", "Платёж между агентами"],
    "Pay another agent or service in paper USDC — the automated payment a data-buying agent would make. Screened, then signed to the ledger.": ["用模拟 USDC 向另一个智能体或服务付款——购买数据的智能体会发起的自动支付。经筛查后签名写入账本。", "Paga a otro agente o servicio en USDC simulados — el pago automático que haría un agente que compra datos. Filtrado y luego firmado en el libro mayor.", "पेपर USDC में किसी अन्य एजेंट या सेवा को भुगतान करें — डेटा खरीदने वाला एजेंट जो स्वचालित भुगतान करता। जाँच के बाद बहीखाते में हस्ताक्षरित।", "ادفع لوكيل أو خدمة أخرى بـ USDC تجريبي — الدفع الآلي الذي يقوم به وكيل يشتري البيانات. يُفحص ثم يُوقّع في الدفتر.", "Pague a outro agente ou serviço em USDC de papel — o pagamento automático que um agente comprador de dados faria. Filtrado e depois assinado no livro-razão.", "Payez un autre agent ou service en USDC papier — le paiement automatique qu'effectuerait un agent acheteur de données. Filtré, puis signé au registre.", "Заплатите другому агенту или сервису бумажными USDC — автоплатёж, который сделал бы агент, покупающий данные. Проверяется, затем подписывается в реестре."],
    "Pay": ["支付", "Pagar", "भुगतान", "ادفع", "Pagar", "Payer", "Оплатить"],
    "Signed action receipts": ["已签名的操作回执", "Recibos de acción firmados", "हस्ताक्षरित कार्रवाई रसीदें", "إيصالات إجراءات موقّعة", "Recibos de ação assinados", "Reçus d'action signés", "Подписанные квитанции действий"],
    "Every action above, recorded on the tamper-evident ledger.": ["以上每项操作都记录在防篡改账本中。", "Cada acción anterior, registrada en el libro mayor a prueba de manipulaciones.", "ऊपर की हर कार्रवाई, छेड़छाड़-स्पष्ट बहीखाते में दर्ज।", "كل إجراء أعلاه، مسجّل في دفتر مقاوم للعبث.", "Cada ação acima, registada no livro-razão à prova de adulteração.", "Chaque action ci-dessus, enregistrée dans le registre inviolable.", "Каждое действие выше записано в защищённый от подделки реестр."],

    // --- markets / coins ---
    "Pick a pair": ["选择交易对", "Elige un par", "एक जोड़ी चुनें", "اختر زوجًا", "Escolha um par", "Choisissez une paire", "Выберите пару"],
    "Every pair on Binance": ["币安上的每个交易对", "Cada par en Binance", "बायनेंस पर हर जोड़ी", "كل زوج على بينانس", "Cada par na Binance", "Chaque paire sur Binance", "Каждая пара на Binance"],
    "Every coin on Binance": ["币安上的每种币", "Cada moneda en Binance", "बायनेंस पर हर सिक्का", "كل عملة على بينانس", "Cada moeda na Binance", "Chaque crypto sur Binance", "Каждая монета на Binance"],
    "Pair detail": ["交易对详情", "Detalle del par", "जोड़ी विवरण", "تفاصيل الزوج", "Detalhe do par", "Détail de la paire", "Детали пары"],
    "Top volume": ["成交量最高", "Mayor volumen", "शीर्ष वॉल्यूम", "الأعلى حجمًا", "Maior volume", "Volume le plus élevé", "Наибольший объём"],
    "Biggest gainers": ["涨幅最大", "Mayores ganancias", "सबसे बड़े लाभ", "الأكثر ارتفاعًا", "Maiores altas", "Plus fortes hausses", "Наибольший рост"],
    "Biggest losers": ["跌幅最大", "Mayores pérdidas", "सबसे बड़े नुकसान", "الأكثر انخفاضًا", "Maiores baixas", "Plus fortes baisses", "Наибольшее падение"],
    "Tightest spread": ["价差最小", "Spread más ajustado", "सबसे कम स्प्रेड", "أضيق فارق سعري", "Spread mais apertado", "Spread le plus serré", "Наименьший спред"],
    "Volume summed across all of a coin's pairs and converted to USD.": ["某币所有交易对的成交量汇总并折算为美元。", "Volumen sumado de todos los pares de una moneda y convertido a USD.", "किसी सिक्के के सभी जोड़ों का वॉल्यूम जोड़कर USD में बदला गया।", "حجم مجمّع عبر جميع أزواج العملة ومحوّل إلى الدولار.", "Volume somado em todos os pares de uma moeda e convertido em USD.", "Volume additionné sur toutes les paires d'une crypto et converti en USD.", "Объём по всем парам монеты, суммированный и переведённый в USD."],
    "Most trades": ["成交笔数最多", "Más operaciones", "सबसे ज़्यादा ट्रेड", "الأكثر صفقات", "Mais operações", "Le plus de transactions", "Больше всего сделок"],
    "A–Z": ["A–Z", "A–Z", "A–Z", "أ–ي", "A–Z", "A–Z", "А–Я"],

    // --- council / sentinel ---
    "Analyze any pair": ["分析任意交易对", "Analiza cualquier par", "किसी भी जोड़ी का विश्लेषण करें", "حلّل أي زوج", "Analise qualquer par", "Analyser n'importe quelle paire", "Проанализировать любую пару"],
    "Analyze": ["分析", "Analizar", "विश्लेषण", "تحليل", "Analisar", "Analyser", "Анализ"],
    "The Council": ["议会", "El Consejo", "परिषद", "المجلس", "O Conselho", "Le Conseil", "Совет"],
    "Threat assessment": ["威胁评估", "Evaluación de amenazas", "खतरा आकलन", "تقييم التهديد", "Avaliação de ameaças", "Évaluation des menaces", "Оценка угроз"],
    "Threat": ["威胁", "Amenaza", "खतरा", "التهديد", "Ameaça", "Menace", "Угроза"],
    "Guardian record": ["守护者记录", "Registro del Guardián", "गार्डियन रिकॉर्ड", "سجل الحارس", "Registo do Guardião", "Journal du Gardien", "Журнал Стража"],
    "Drill the Guardian": ["演练守护者", "Probar el Guardián", "गार्डियन का अभ्यास करें", "تدريب الحارس", "Testar o Guardião", "Tester le Gardien", "Проверить Стража"],
    "Defensive actions": ["防御性操作", "Acciones defensivas", "रक्षात्मक कार्रवाई", "إجراءات دفاعية", "Ações defensivas", "Actions défensives", "Защитные действия"],
    "Clear quarantine": ["解除隔离", "Limpiar cuarentena", "क्वारंटीन साफ़ करें", "إزالة الحجر", "Limpar quarentena", "Lever la quarantaine", "Снять карантин"],
    "Calm drift": ["平静漂移", "Deriva tranquila", "शांत बहाव", "انجراف هادئ", "Deriva calma", "Dérive calme", "Спокойный дрейф"],
    "Cascading crash": ["级联崩盘", "Caída en cascada", "श्रृंखलाबद्ध क्रैश", "انهيار متتالٍ", "Queda em cascata", "Krach en cascade", "Каскадный обвал"],
    "Crash the whole book": ["使整个订单簿崩溃", "Colapsar todo el libro", "पूरी बुक क्रैश करें", "انهيار الدفتر بالكامل", "Colapsar todo o livro", "Faire s'effondrer tout le carnet", "Обрушить весь стакан"],
    "Grinding bear": ["缓慢下行", "Bajista persistente", "धीमा बियर", "هبوط بطيء", "Baixa persistente", "Baissier lent", "Вялый медвежий рынок"],
    "Trending bull": ["趋势上行", "Alcista con tendencia", "ट्रेंडिंग बुल", "صعود قوي", "Alta com tendência", "Haussier en tendance", "Трендовый бычий рынок"],
    "Violent chop": ["剧烈震荡", "Vaivén violento", "तीव्र उतार-चढ़ाव", "تذبذب عنيف", "Oscilação violenta", "Balancement violent", "Резкая пила"],
    "Shock": ["冲击", "Choque", "झटका", "صدمة", "Choque", "Choc", "Шок"],
    "Inject": ["注入", "Inyectar", "इंजेक्ट", "حقن", "Injetar", "Injecter", "Внедрить"],

    // --- narratives / yield / macro ---
    "Narrative strength": ["叙事强度", "Fuerza de la narrativa", "आख्यान की ताकत", "قوة السردية", "Força da narrativa", "Force du narratif", "Сила нарратива"],
    "Where idle cash sits": ["闲置资金存放处", "Dónde está el efectivo inactivo", "निष्क्रिय नकदी कहाँ है", "أين يوجد النقد الخامل", "Onde fica o dinheiro parado", "Où se trouve la trésorerie inactive", "Где лежит свободный кэш"],
    "Current routing": ["当前路由", "Enrutamiento actual", "वर्तमान रूटिंग", "التوجيه الحالي", "Encaminhamento atual", "Routage actuel", "Текущая маршрутизация"],
    "Binance Earn and DeFi compared in one ranking": ["将币安理财与 DeFi 放在同一排名中比较", "Binance Earn y DeFi comparados en un solo ranking", "बायनेंस Earn और DeFi की एक ही रैंकिंग में तुलना", "مقارنة Binance Earn وDeFi في تصنيف واحد", "Binance Earn e DeFi comparados num único ranking", "Binance Earn et DeFi comparés dans un seul classement", "Binance Earn и DeFi в одном рейтинге"],
    "Ranked by risk, not recency": ["按风险排序，而非时间", "Ordenado por riesgo, no por lo reciente", "जोखिम के अनुसार क्रमबद्ध, नवीनता से नहीं", "مرتّب حسب المخاطرة لا الحداثة", "Ordenado por risco, não por recência", "Classé par risque, pas par récence", "Отсортировано по риску, а не по свежести"],
    "US macro regime": ["美国宏观格局", "Régimen macro de EE. UU.", "यूएस मैक्रो रिजीम", "النظام الاقتصادي الكلي الأمريكي", "Regime macro dos EUA", "Régime macro américain", "Макрорежим США"],
    "Data spending": ["数据支出", "Gasto en datos", "डेटा खर्च", "إنفاق البيانات", "Gasto em dados", "Dépenses de données", "Расходы на данные"],
    "Binance x402, metered per request and capped locally": ["币安 x402，按请求计量并在本地设上限", "Binance x402, medido por solicitud y limitado localmente", "बायनेंस x402, प्रति अनुरोध मीटर और स्थानीय रूप से सीमित", "Binance x402، محسوب لكل طلب ومحدود محليًا", "Binance x402, medido por pedido e limitado localmente", "Binance x402, mesuré par requête et plafonné localement", "Binance x402, учёт по запросам и локальный лимит"],
    "Binance meters by request weight, not request count.": ["币安按请求权重计量，而非请求次数。", "Binance mide por peso de la solicitud, no por número de solicitudes.", "बायनेंस अनुरोध भार से मापता है, अनुरोध संख्या से नहीं।", "بينانس يقيس حسب وزن الطلب لا عدد الطلبات.", "A Binance mede por peso do pedido, não por número de pedidos.", "Binance mesure par poids de requête, pas par nombre de requêtes.", "Binance считает по весу запроса, а не по их числу."],

    // --- events / ledger ---
    "Headlines being monitored": ["正在监控的头条", "Titulares monitoreados", "निगरानी में सुर्खियाँ", "العناوين قيد المراقبة", "Manchetes monitorizadas", "Titres surveillés", "Отслеживаемые заголовки"],
    "News changes risk posture, never direction.": ["新闻改变风险姿态，而非方向。", "Las noticias cambian la postura de riesgo, nunca la dirección.", "समाचार जोखिम रुख बदलते हैं, दिशा कभी नहीं।", "الأخبار تغيّر موقف المخاطرة لا الاتجاه.", "As notícias mudam a postura de risco, nunca a direção.", "Les actualités changent la posture de risque, jamais la direction.", "Новости меняют риск-позицию, но не направление."],
    "Sources": ["来源", "Fuentes", "स्रोत", "المصادر", "Fontes", "Sources", "Источники"],
    "Decision ledger": ["决策账本", "Libro de decisiones", "निर्णय बहीखाता", "دفتر القرارات", "Livro de decisões", "Registre des décisions", "Реестр решений"],
    "Each one carries the hash of the reasoning that produced it.": ["每一条都带有产生它的推理哈希。", "Cada una lleva el hash del razonamiento que la produjo.", "प्रत्येक में उसे उत्पन्न करने वाले तर्क का हैश होता है।", "كل واحدة تحمل بصمة الاستدلال الذي أنتجها.", "Cada uma carrega o hash do raciocínio que a produziu.", "Chacune porte le hash du raisonnement qui l'a produite.", "Каждая несёт хеш породившего её рассуждения."],
    "External anchors": ["外部锚点", "Anclas externas", "बाहरी एंकर", "مراسٍ خارجية", "Âncoras externas", "Ancres externes", "Внешние якоря"],
    "Anchor now": ["立即锚定", "Anclar ahora", "अभी एंकर करें", "التثبيت الآن", "Ancorar agora", "Ancrer maintenant", "Закрепить сейчас"],
    "Verify anchors": ["验证锚点", "Verificar anclas", "एंकर सत्यापित करें", "التحقق من المراسي", "Verificar âncoras", "Vérifier les ancres", "Проверить якоря"],
    "Verify chain": ["验证链", "Verificar cadena", "श्रृंखला सत्यापित करें", "التحقق من السلسلة", "Verificar cadeia", "Vérifier la chaîne", "Проверить цепочку"],
    "Your rules": ["你的规则", "Tus reglas", "आपके नियम", "قواعدك", "As suas regras", "Vos règles", "Ваши правила"],
    "Reload from file": ["从文件重新加载", "Recargar desde archivo", "फ़ाइल से पुनः लोड करें", "إعادة التحميل من الملف", "Recarregar do ficheiro", "Recharger depuis le fichier", "Перезагрузить из файла"],
    "Records": ["记录", "Registros", "रिकॉर्ड", "السجلات", "Registos", "Enregistrements", "Записи"],

    // --- backtest ---
    "Backtest on real Binance history": ["基于真实币安历史回测", "Backtest sobre historial real de Binance", "असली बायनेंस इतिहास पर बैकटेस्ट", "اختبار رجعي على سجل بينانس الحقيقي", "Backtest sobre histórico real da Binance", "Backtest sur l'historique réel de Binance", "Бэктест на реальной истории Binance"],
    "Genuine candles, replayed bar by bar with no look-ahead.": ["真实K线，逐根回放，无未来函数。", "Velas genuinas, reproducidas barra por barra sin anticipación.", "असली कैंडल, बार-दर-बार बिना लुक-अहेड के पुनः चलाई गईं।", "شموع حقيقية، معادة شمعة بشمعة دون استباق.", "Velas genuínas, reproduzidas barra a barra sem antecipação.", "Bougies authentiques, rejouées barre par barre sans anticipation.", "Настоящие свечи, воспроизведённые бар за баром без заглядывания вперёд."],
    "Run backtest": ["运行回测", "Ejecutar backtest", "बैकटेस्ट चलाएँ", "تشغيل الاختبار الرجعي", "Executar backtest", "Lancer le backtest", "Запустить бэктест"],
    "Horizon (bars)": ["时间范围（根数）", "Horizonte (barras)", "क्षितिज (बार)", "الأفق (شموع)", "Horizonte (barras)", "Horizon (barres)", "Горизонт (бары)"],

    // --- misc labels / buttons ---
    "Refresh": ["刷新", "Actualizar", "रिफ़्रेश", "تحديث", "Atualizar", "Actualiser", "Обновить"],
    "Refresh now": ["立即刷新", "Actualizar ahora", "अभी रिफ़्रेश करें", "تحديث الآن", "Atualizar agora", "Actualiser maintenant", "Обновить сейчас"],
    "Apply": ["应用", "Aplicar", "लागू करें", "تطبيق", "Aplicar", "Appliquer", "Применить"],
    "Connect": ["连接", "Conectar", "कनेक्ट", "اتصال", "Conectar", "Connecter", "Подключить"],
    "Close": ["关闭", "Cerrar", "बंद करें", "إغلاق", "Fechar", "Fermer", "Закрыть"],
    "Check first": ["先检查", "Comprobar primero", "पहले जाँचें", "تحقّق أولًا", "Verificar primeiro", "Vérifier d'abord", "Сначала проверить"],
    "Regime": ["市场格局", "Régimen", "रिजीम", "النظام", "Regime", "Régime", "Режим"],
    "Data health": ["数据健康度", "Salud de datos", "डेटा स्वास्थ्य", "سلامة البيانات", "Saúde dos dados", "Santé des données", "Состояние данных"],
    "How this is scored": ["评分方式", "Cómo se puntúa", "इसे कैसे स्कोर किया जाता है", "كيف يُحتسب هذا", "Como isto é pontuado", "Comment c'est noté", "Как это оценивается"],
    "Every call is graded against what the market actually did.": ["每个判断都对照市场的真实走势评分。", "Cada decisión se califica contra lo que el mercado realmente hizo.", "हर कॉल को बाज़ार ने वास्तव में जो किया उसके विरुद्ध आँका जाता है।", "كل توقّع يُقيَّم مقابل ما فعله السوق فعلًا.", "Cada decisão é avaliada face ao que o mercado realmente fez.", "Chaque appel est noté par rapport à ce que le marché a réellement fait.", "Каждый прогноз оценивается по тому, что рынок сделал на деле."],
    "What this is for.": ["用途说明。", "Para qué sirve esto.", "यह किसलिए है।", "ما الغرض من هذا.", "Para que serve isto.", "À quoi cela sert.", "Для чего это."],

    // --- desk suggestion chips (display only; the query is sent from data-q in English) ---
    "How's the market?": ["市场怎么样？", "¿Cómo está el mercado?", "बाज़ार कैसा है?", "كيف حال السوق؟", "Como está o mercado?", "Comment va le marché ?", "Как рынок?"],
    "How risky is BTC?": ["BTC 风险有多大？", "¿Qué tan arriesgado es BTC?", "BTC कितना जोखिम भरा है?", "ما مدى خطورة BTC؟", "Quão arriscado é o BTC?", "Quel est le risque du BTC ?", "Насколько рискован BTC?"],
    "What do you think of SOL?": ["你怎么看 SOL？", "¿Qué opinas de SOL?", "SOL के बारे में क्या सोचते हैं?", "ما رأيك في SOL؟", "O que acha do SOL?", "Que pensez-vous de SOL ?", "Что думаешь о SOL?"],
    "Key levels for ETH": ["ETH 的关键点位", "Niveles clave de ETH", "ETH के मुख्य स्तर", "المستويات الرئيسية لـ ETH", "Níveis-chave do ETH", "Niveaux clés de l'ETH", "Ключевые уровни ETH"],
    "Are the analysts any good?": ["这些分析师靠谱吗？", "¿Son buenos los analistas?", "क्या विश्लेषक अच्छे हैं?", "هل المحللون جيدون؟", "Os analistas são bons?", "Les analystes sont-ils bons ?", "А аналитики хороши?"],

    // --- runtime-rendered strings (cockpit / session / tables / status) ---
    "Paper mode. Fills are simulated and no order reaches Binance. Switch to bridge mode to have GlassBox hand signed instructions to the Binance MCP server for your confirmation.": ["模拟模式。成交为模拟，不会有订单发送到币安。切换到桥接模式，可让 GlassBox 向币安 MCP 服务器发送已签名指令，交由你确认。", "Modo simulado. Las ejecuciones son simuladas y ninguna orden llega a Binance. Cambia al modo puente para que GlassBox entregue instrucciones firmadas al servidor MCP de Binance para tu confirmación.", "पेपर मोड। फिल सिम्युलेटेड हैं और कोई ऑर्डर बायनेंस तक नहीं पहुँचता। ब्रिज मोड पर स्विच करें ताकि GlassBox आपकी पुष्टि के लिए बायनेंस MCP सर्वर को हस्ताक्षरित निर्देश भेजे।", "الوضع التجريبي. عمليات التنفيذ محاكاة ولا يصل أي أمر إلى بينانس. بدّل إلى وضع الجسر ليرسل GlassBox تعليمات موقّعة إلى خادم MCP في بينانس لتأكيدك.", "Modo de papel. As execuções são simuladas e nenhuma ordem chega à Binance. Mude para o modo ponte para que a GlassBox entregue instruções assinadas ao servidor MCP da Binance para a sua confirmação.", "Mode papier. Les exécutions sont simulées et aucun ordre n'atteint Binance. Passez en mode passerelle pour que GlassBox transmette des instructions signées au serveur MCP de Binance pour votre confirmation.", "Бумажный режим. Исполнения имитируются, и ни один ордер не доходит до Binance. Переключитесь в режим моста, чтобы GlassBox передавал подписанные инструкции на MCP-сервер Binance для вашего подтверждения."],
    "A single bar showing what every dollar of the account is currently doing, split by percentage into the four states below. It's grey right now because 100% of the account is idle — no position is open, so there's nothing yet to color yellow, blue, or red. Open a position and this bar will visibly split.": ["一根进度条，显示账户中每一美元当前的状态，按百分比分为下方四种状态。现在为灰色，因为账户 100% 处于闲置——没有持仓，所以还没有黄、蓝或红色可显示。开仓后，这根条会明显分段。", "Una sola barra que muestra qué hace cada dólar de la cuenta ahora mismo, dividida por porcentaje en los cuatro estados de abajo. Está gris porque el 100% de la cuenta está inactivo: no hay ninguna posición abierta, así que aún no hay nada que colorear de amarillo, azul o rojo. Abre una posición y esta barra se dividirá visiblemente.", "एक बार जो दिखाता है कि खाते का हर डॉलर अभी क्या कर रहा है, नीचे दिए चार राज्यों में प्रतिशत के अनुसार विभाजित। यह अभी ग्रे है क्योंकि खाता 100% निष्क्रिय है — कोई पोज़िशन खुली नहीं, इसलिए अभी पीला, नीला या लाल रंग देने को कुछ नहीं। एक पोज़िशन खोलें और यह बार स्पष्ट रूप से विभाजित होगा।", "شريط واحد يوضح ما يفعله كل دولار في الحساب حاليًا، مقسّمًا بالنسبة المئوية إلى الحالات الأربع أدناه. لونه رمادي الآن لأن 100% من الحساب خامل — لا يوجد مركز مفتوح، لذا لا شيء بعد ليُلوَّن بالأصفر أو الأزرق أو الأحمر. افتح مركزًا وسينقسم هذا الشريط بوضوح.", "Uma única barra que mostra o que cada dólar da conta está a fazer agora, dividida por percentagem nos quatro estados abaixo. Está cinzenta porque 100% da conta está parada — nenhuma posição está aberta, por isso ainda não há nada para colorir de amarelo, azul ou vermelho. Abra uma posição e esta barra dividir-se-á visivelmente.", "Une seule barre montrant ce que fait chaque dollar du compte en ce moment, répartie en pourcentage entre les quatre états ci-dessous. Elle est grise car 100 % du compte est inactif : aucune position n'est ouverte, donc rien encore à colorer en jaune, bleu ou rouge. Ouvrez une position et cette barre se divisera visiblement.", "Одна полоса показывает, что сейчас делает каждый доллар счёта, разбитая по процентам на четыре состояния ниже. Сейчас она серая, потому что 100% счёта простаивает — ни одной открытой позиции, поэтому пока нечего окрашивать в жёлтый, синий или красный. Откройте позицию, и эта полоса заметно разделится."],
    "Idle — cash and yield": ["闲置 — 现金与收益", "Inactivo — efectivo y rendimiento", "निष्क्रिय — नकद और यील्ड", "خامل — نقد وعائد", "Parado — dinheiro e rendimento", "Inactif — liquidités et rendement", "Простаивает — кэш и доход"],
    "Deployed — conviction positions": ["已部署 — 高信念头寸", "Desplegado — posiciones de convicción", "तैनात — कन्विक्शन पोज़िशन", "منشور — مراكز عالية الثقة", "Implantado — posições de convicção", "Déployé — positions de conviction", "Развёрнуто — уверенные позиции"],
    "Hedged — defensive leg on": ["已对冲 — 防御性头寸开启", "Cubierto — pata defensiva activa", "हेज्ड — रक्षात्मक लेग चालू", "محوّط — ساق دفاعية مفعّلة", "Coberto — perna defensiva ativa", "Couvert — jambe défensive active", "Захеджировано — защитная нога включена"],
    "Quarantined — Guardian lockdown": ["已隔离 — 守护者锁定", "En cuarentena — bloqueo del Guardián", "क्वारंटीन — गार्डियन लॉकडाउन", "محجور — إغلاق الحارس", "Em quarentena — bloqueio do Guardião", "En quarantaine — verrouillage du Gardien", "В карантине — блокировка Стража"],
    "Cash": ["现金", "Efectivo", "नकद", "النقد", "Dinheiro", "Liquidités", "Наличные"],
    "In yield": ["生息中", "En rendimiento", "यील्ड में", "في العائد", "Em rendimento", "En rendement", "В доходности"],
    "Gross exposure": ["总敞口", "Exposición bruta", "सकल एक्सपोज़र", "التعرض الإجمالي", "Exposição bruta", "Exposition brute", "Валовая экспозиция"],
    "Closed trades": ["已平仓交易", "Operaciones cerradas", "बंद ट्रेड", "الصفقات المغلقة", "Operações fechadas", "Transactions clôturées", "Закрытые сделки"],
    "Win rate": ["胜率", "Tasa de acierto", "जीत दर", "نسبة الفوز", "Taxa de acerto", "Taux de réussite", "Доля выигрышей"],
    "Profit factor": ["盈利因子", "Factor de beneficio", "प्रॉफ़िट फ़ैक्टर", "عامل الربح", "Fator de lucro", "Facteur de profit", "Профит-фактор"],
    "Fees paid": ["已付手续费", "Comisiones pagadas", "भुगतान शुल्क", "الرسوم المدفوعة", "Taxas pagas", "Frais payés", "Уплаченные комиссии"],
    "Yield earned": ["已赚收益", "Rendimiento ganado", "अर्जित यील्ड", "العائد المكتسب", "Rendimento ganho", "Rendement gagné", "Полученный доход"],
    "Intents raised": ["发起的意图", "Intenciones generadas", "उठाए गए इंटेंट", "النوايا المطروحة", "Intenções geradas", "Intentions émises", "Поданные намерения"],
    "Blocked by rules": ["被规则拦截", "Bloqueadas por reglas", "नियमों द्वारा अवरुद्ध", "محظورة بالقواعد", "Bloqueadas por regras", "Bloquées par les règles", "Заблокировано правилами"],
    "Guardian vetoes": ["守护者否决", "Vetos del Guardián", "गार्डियन वीटो", "اعتراضات الحارس", "Vetos do Guardião", "Vetos du Gardien", "Вето Стража"],
    "Net P&L": ["净盈亏", "P&L neto", "शुद्ध P&L", "صافي الربح والخسارة", "P&L líquido", "P&L net", "Чистый P&L"],
    "Max drawdown": ["最大回撤", "Caída máxima", "अधिकतम ड्रॉडाउन", "أقصى تراجع", "Recuo máximo", "Repli maximal", "Максимальная просадка"],
    "OFF — manual only": ["关闭 — 仅手动", "DESACTIVADO — solo manual", "बंद — केवल मैनुअल", "متوقف — يدوي فقط", "DESLIGADO — apenas manual", "DÉSACTIVÉ — manuel uniquement", "ВЫКЛ — только вручную"],
    "Disable autonomous trading": ["禁用自主交易", "Desactivar trading autónomo", "स्वायत्त ट्रेडिंग अक्षम करें", "تعطيل التداول الذاتي", "Desativar trading autónomo", "Désactiver le trading autonome", "Отключить автономную торговлю"],
    "When on, the engine opens positions by itself following the Council and Narrative scout — every one still checked by the Constitution and Guardian.": ["开启后，引擎会依据议会与叙事侦察自行开仓，但每一笔仍受章程与守护者检查。", "Cuando está activado, el motor abre posiciones por sí mismo siguiendo al Consejo y al explorador de narrativas, pero cada una sigue siendo revisada por la Constitución y el Guardián.", "चालू होने पर, इंजन परिषद और नैरेटिव स्काउट का अनुसरण करते हुए स्वयं पोज़िशन खोलता है, फिर भी हर एक की जाँच संविधान और गार्डियन करते हैं।", "عند التفعيل، يفتح المحرك المراكز بنفسه وفقًا للمجلس وكاشف السرديات، لكن كل مركز يظل خاضعًا لفحص الدستور والحارس.", "Quando ativado, o motor abre posições sozinho seguindo o Conselho e o explorador de narrativas, mas cada uma ainda é verificada pela Constituição e pelo Guardião.", "Une fois activé, le moteur ouvre des positions de lui-même en suivant le Conseil et l'éclaireur de narratifs, chacune restant vérifiée par la Constitution et le Gardien.", "Когда включён, движок сам открывает позиции, следуя Совету и разведчику нарративов, но каждая по-прежнему проверяется Конституцией и Стражем."],
    "Pause engine": ["暂停引擎", "Pausar motor", "इंजन रोकें", "إيقاف المحرك مؤقتًا", "Pausar motor", "Mettre le moteur en pause", "Приостановить движок"],
    "Start the engine to watch decisions arrive.": ["启动引擎以查看决策到达。", "Inicia el motor para ver llegar las decisiones.", "निर्णय आते देखने के लिए इंजन शुरू करें।", "شغّل المحرك لرؤية القرارات تصل.", "Inicie o motor para ver as decisões chegarem.", "Démarrez le moteur pour voir arriver les décisions.", "Запустите движок, чтобы видеть поступающие решения."],
    "The Council has not met yet": ["议会尚未召开", "El Consejo aún no se ha reunido", "परिषद अभी नहीं मिली", "لم يجتمع المجلس بعد", "O Conselho ainda não se reuniu", "Le Conseil ne s'est pas encore réuni", "Совет ещё не собирался"],
    "No open positions": ["无持仓", "Sin posiciones abiertas", "कोई खुली पोज़िशन नहीं", "لا مراكز مفتوحة", "Sem posições abertas", "Aucune position ouverte", "Нет открытых позиций"],
    "No open futures positions": ["无合约持仓", "Sin posiciones de futuros abiertas", "कोई खुली फ्यूचर्स पोज़िशन नहीं", "لا مراكز عقود آجلة مفتوحة", "Sem posições de futuros abertas", "Aucune position futures ouverte", "Нет открытых фьючерсных позиций"],
    "No decisions yet": ["暂无决策", "Aún no hay decisiones", "अभी कोई निर्णय नहीं", "لا قرارات بعد", "Ainda sem decisões", "Aucune décision pour l'instant", "Пока нет решений"],
    "Side": ["方向", "Lado", "साइड", "الجهة", "Lado", "Sens", "Сторона"],
    "Size": ["数量", "Tamaño", "साइज़", "الحجم", "Tamanho", "Taille", "Размер"],
    "Price": ["价格", "Precio", "मूल्य", "السعر", "Preço", "Prix", "Цена"],
    "Balance": ["余额", "Saldo", "बैलेंस", "الرصيد", "Saldo", "Solde", "Баланс"],
    "Asset": ["资产", "Activo", "एसेट", "الأصل", "Ativo", "Actif", "Актив"],
    "Amount": ["金额", "Importe", "राशि", "المبلغ", "Montante", "Montant", "Сумма"],
    "Symbol": ["代码", "Símbolo", "प्रतीक", "الرمز", "Símbolo", "Symbole", "Символ"],
    "Status": ["状态", "Estado", "स्थिति", "الحالة", "Estado", "Statut", "Статус"],
    "Type": ["类型", "Tipo", "प्रकार", "النوع", "Tipo", "Type", "Тип"],
    "Value": ["价值", "Valor", "मूल्य", "القيمة", "Valor", "Valeur", "Значение"],
    "Change": ["涨跌", "Cambio", "परिवर्तन", "التغيّر", "Variação", "Variation", "Изменение"],
    "Entry": ["入场价", "Entrada", "एंट्री", "الدخول", "Entrada", "Entrée", "Вход"],
    "Mark": ["标记价", "Precio de marca", "मार्क", "السعر المرجعي", "Preço de marca", "Prix de marque", "Марк-цена"],
    "Notional": ["名义价值", "Nocional", "नोशनल", "القيمة الاسمية", "Nocional", "Notionnel", "Номинал"],
    "Margin": ["保证金", "Margen", "मार्जिन", "الهامش", "Margem", "Marge", "Маржа"],
    "Result": ["结果", "Resultado", "परिणाम", "النتيجة", "Resultado", "Résultat", "Результат"],
    "Time": ["时间", "Hora", "समय", "الوقت", "Hora", "Heure", "Время"],
    "Date": ["日期", "Fecha", "तिथि", "التاريخ", "Data", "Date", "Дата"],
    "Event": ["事件", "Evento", "घटना", "الحدث", "Evento", "Événement", "Событие"],
    "Rule": ["规则", "Regla", "नियम", "القاعدة", "Regra", "Règle", "Правило"],
    "Risk": ["风险", "Riesgo", "जोखिम", "المخاطرة", "Risco", "Risque", "Риск"],
    "Approve": ["批准", "Aprobar", "स्वीकृत करें", "موافقة", "Aprovar", "Approuver", "Одобрить"],
    "Reject": ["拒绝", "Rechazar", "अस्वीकार करें", "رفض", "Rejeitar", "Rejeter", "Отклонить"],
    "Vote weight": ["投票权重", "Peso de voto", "वोट भार", "وزن التصويت", "Peso de voto", "Poids de vote", "Вес голоса"],
    "disconnected": ["已断开", "desconectado", "डिस्कनेक्टेड", "غير متصل", "desligado", "déconnecté", "отключено"],
    "data offline": ["数据离线", "datos sin conexión", "डेटा ऑफ़लाइन", "البيانات غير متصلة", "dados offline", "données hors ligne", "данные офлайн"],
    "Off by default — the engine analyses and shows its reasoning, but won't open positions on its own until you allow it.": ["默认关闭——引擎会分析并展示其推理，但在你允许之前不会自行开仓。", "Desactivado por defecto: el motor analiza y muestra su razonamiento, pero no abrirá posiciones por sí solo hasta que lo permitas.", "डिफ़ॉल्ट रूप से बंद — इंजन विश्लेषण करता है और अपना तर्क दिखाता है, पर आपकी अनुमति तक स्वयं पोज़िशन नहीं खोलता।", "متوقف افتراضيًا — يحلّل المحرك ويعرض استدلاله، لكنه لن يفتح مراكز من تلقاء نفسه حتى تسمح بذلك.", "Desligado por defeito — o motor analisa e mostra o seu raciocínio, mas não abrirá posições sozinho até você permitir.", "Désactivé par défaut — le moteur analyse et montre son raisonnement, mais n'ouvrira pas de positions de lui-même tant que vous ne l'autorisez pas.", "Выключено по умолчанию — движок анализирует и показывает свои рассуждения, но не откроет позиции сам, пока вы не разрешите."],

    // --- Control Center ---
    "Currently running in Paper mode.": ["当前运行于模拟模式。", "Actualmente en modo simulado.", "अभी पेपर मोड में चल रहा है।", "يعمل حاليًا في الوضع التجريبي.", "Atualmente em modo de papel.", "Actuellement en mode papier.", "Сейчас работает в бумажном режиме."],
    "Everything below used to be a shell command. It isn't any more.": ["下面的一切过去都是命令行操作，现在不再是了。", "Todo lo de abajo solía ser un comando de terminal. Ya no lo es.", "नीचे दी हर चीज़ पहले शेल कमांड थी। अब नहीं।", "كل ما في الأسفل كان أمر طرفية سابقًا. لم يعد كذلك.", "Tudo abaixo costumava ser um comando de terminal. Já não é.", "Tout ce qui suit était auparavant une commande shell. Ce n'est plus le cas.", "Всё, что ниже, раньше было командой в терминале. Больше нет."],
    "Five seeded market conditions, run in an isolated engine that never touches your real ledger.": ["五种预设市场情形，在与真实账本完全隔离的引擎中运行。", "Cinco condiciones de mercado predefinidas, ejecutadas en un motor aislado que nunca toca tu libro real.", "पाँच सीड किए गए बाज़ार हालात, एक पृथक इंजन में चलते हैं जो आपके असली बहीखाते को कभी नहीं छूता।", "خمس حالات سوق مُعدّة مسبقًا، تعمل في محرك معزول لا يمسّ دفترك الحقيقي أبدًا.", "Cinco condições de mercado pré-definidas, executadas num motor isolado que nunca toca no seu livro-razão real.", "Cinq conditions de marché prédéfinies, exécutées dans un moteur isolé qui ne touche jamais votre registre réel.", "Пять заданных рыночных условий выполняются в изолированном движке, который никогда не затрагивает ваш реальный реестр."],
    "Optional. Off uses the seeded simulator instead of real Binance prices.": ["可选。关闭时使用预设模拟器，而非币安真实价格。", "Opcional. Desactivado usa el simulador predefinido en lugar de precios reales de Binance.", "वैकल्पिक। बंद होने पर असली बायनेंस कीमतों के बजाय सीड किए सिम्युलेटर का उपयोग होता है।", "اختياري. عند الإيقاف يُستخدم المحاكي المُعد مسبقًا بدل أسعار بينانس الحقيقية.", "Opcional. Desligado usa o simulador pré-definido em vez de preços reais da Binance.", "Facultatif. Désactivé, utilise le simulateur prédéfini au lieu des prix réels de Binance.", "Необязательно. При выключении используется заданный симулятор вместо реальных цен Binance."],
    "Real Binance data, simulated fills.": ["币安真实数据，模拟成交。", "Datos reales de Binance, ejecuciones simuladas.", "असली बायनेंस डेटा, सिम्युलेटेड फिल।", "بيانات بينانس حقيقية، تنفيذ محاكى.", "Dados reais da Binance, execuções simuladas.", "Données réelles de Binance, exécutions simulées.", "Реальные данные Binance, имитированные исполнения."],
    "Real orders in your Agentic sub-account on Binance.": ["在你币安的智能体子账户中下达真实订单。", "Órdenes reales en tu subcuenta Agentic de Binance.", "बायनेंस पर आपके Agentic सब-अकाउंट में असली ऑर्डर।", "أوامر حقيقية في حسابك الفرعي Agentic على بينانس.", "Ordens reais na sua subconta Agentic na Binance.", "De vrais ordres dans votre sous-compte Agentic sur Binance.", "Реальные ордера в вашем субсчёте Agentic на Binance."],
    "Signed instructions emitted for you to run yourself.": ["生成已签名指令，供你自行执行。", "Instrucciones firmadas emitidas para que las ejecutes tú mismo.", "आपके स्वयं चलाने के लिए हस्ताक्षरित निर्देश जारी किए गए।", "تُصدَر تعليمات موقّعة لتنفّذها بنفسك.", "Instruções assinadas emitidas para você executar.", "Instructions signées émises pour que vous les exécutiez vous-même.", "Подписанные инструкции для самостоятельного выполнения."],
    "Simulated fills. Nothing reaches Binance.": ["模拟成交。不会有任何内容发送到币安。", "Ejecuciones simuladas. Nada llega a Binance.", "सिम्युलेटेड फिल। कुछ भी बायनेंस तक नहीं पहुँचता।", "تنفيذ محاكى. لا شيء يصل إلى بينانس.", "Execuções simuladas. Nada chega à Binance.", "Exécutions simulées. Rien n'atteint Binance.", "Имитированные исполнения. Ничего не доходит до Binance."],
    "Twelve adversarial checks, in a disposable engine and ledger — this can never touch your real audit trail.": ["十二项对抗性检查，在一次性引擎与账本中运行——绝不会触及你的真实审计记录。", "Doce comprobaciones adversas, en un motor y libro desechables: nunca puede tocar tu registro de auditoría real.", "बारह प्रतिकूल जाँचें, एक डिस्पोज़ेबल इंजन और बहीखाते में — यह आपके असली ऑडिट ट्रेल को कभी नहीं छू सकता।", "اثنا عشر فحصًا عدائيًا، في محرك ودفتر مؤقتين — لا يمكن أن يمسّ سجل تدقيقك الحقيقي أبدًا.", "Doze verificações adversariais, num motor e livro descartáveis — nunca pode tocar no seu registo de auditoria real.", "Douze contrôles antagonistes, dans un moteur et un registre jetables — cela ne peut jamais toucher votre piste d'audit réelle.", "Двенадцать состязательных проверок в одноразовом движке и реестре — это никогда не затронет ваш реальный аудит-журнал."],
    "Live": ["实盘", "En vivo", "लाइव", "مباشر", "Ao vivo", "En direct", "Вживую"],
    "Paper": ["模拟", "Simulado", "पेपर", "تجريبي", "Papel", "Papier", "Бумажный"],
    "Shadow": ["影子", "Sombra", "शैडो", "ظل", "Sombra", "Ombre", "Теневой"],
    "Mock": ["仿真", "Simulacro", "मॉक", "وهمي", "Simulado", "Fictif", "Имитация"],
    "Ticks": ["跳动", "Ticks", "टिक", "نبضات", "Ticks", "Ticks", "Тики"],
    "— a severe, fast decline at extreme volatility: tests whether the Guardian actually protects capital when it matters most.": ["—在极端波动下的剧烈快速下跌：检验守护者在最关键时是否真正保护资金。", "— un desplome severo y rápido con volatilidad extrema: prueba si el Guardián realmente protege el capital cuando más importa.", "— अत्यधिक अस्थिरता में तीव्र, तेज़ गिरावट: परखता है कि गार्डियन सबसे अहम समय पर पूंजी की वास्तव में रक्षा करता है या नहीं।", "— هبوط حاد وسريع عند تقلب شديد: يختبر ما إذا كان الحارس يحمي رأس المال فعلًا عند الأهمية القصوى.", "— uma queda severa e rápida com volatilidade extrema: testa se o Guardião protege mesmo o capital quando mais importa.", "— une chute sévère et rapide à volatilité extrême : teste si le Gardien protège réellement le capital au moment crucial.", "— резкое быстрое падение при экстремальной волатильности: проверяет, действительно ли Страж защищает капитал в критический момент."],
    "— small steady gains, low volatility: an ordinary quiet market, the baseline every other scenario is measured against.": ["—小幅稳定上涨、低波动：普通的平静市场，作为衡量其他情形的基准。", "— pequeñas ganancias estables, baja volatilidad: un mercado tranquilo normal, la referencia con la que se miden los demás escenarios.", "— छोटे स्थिर लाभ, कम अस्थिरता: एक सामान्य शांत बाज़ार, वह आधार जिससे हर दूसरा परिदृश्य मापा जाता है।", "— مكاسب صغيرة ثابتة، تقلب منخفض: سوق هادئ عادي، الأساس الذي تُقاس به بقية السيناريوهات.", "— pequenos ganhos estáveis, baixa volatilidade: um mercado calmo comum, a base pela qual todos os outros cenários são medidos.", "— de petits gains réguliers, faible volatilité : un marché calme ordinaire, la référence à laquelle tous les autres scénarios sont comparés.", "— небольшой стабильный рост, низкая волатильность: обычный спокойный рынок, эталон для всех прочих сценариев."],
    "— sustained decline at elevated volatility: tests whether losses are cut early rather than allowed to compound.": ["—在升高波动下的持续下跌：检验是否及早止损，而非任其累积。", "— descenso sostenido con volatilidad elevada: prueba si las pérdidas se cortan pronto en vez de dejar que se acumulen.", "— बढ़ी अस्थिरता में निरंतर गिरावट: परखता है कि नुकसान जल्दी काटे जाते हैं या बढ़ने दिए जाते हैं।", "— انخفاض متواصل عند تقلب مرتفع: يختبر ما إذا كانت الخسائر تُقطع مبكرًا بدل تركها تتراكم.", "— declínio sustentado com volatilidade elevada: testa se as perdas são cortadas cedo em vez de deixadas acumular.", "— baisse prolongée à volatilité élevée : teste si les pertes sont coupées tôt plutôt que laissées s'accumuler.", "— устойчивое падение при повышенной волатильности: проверяет, срезаются ли убытки рано, а не накапливаются."],
    "— sustained upward moves at moderate volatility: tests whether risk controls needlessly hold the system back during a genuine rally.": ["—在中等波动下的持续上涨：检验风险控制是否在真正的上涨行情中不必要地拖累系统。", "— movimientos alcistas sostenidos con volatilidad moderada: prueba si los controles de riesgo frenan innecesariamente el sistema durante un repunte genuino.", "— मध्यम अस्थिरता में निरंतर ऊपर की चाल: परखता है कि जोखिम नियंत्रण असली तेज़ी के दौरान सिस्टम को अनावश्यक रूप से रोकते हैं या नहीं।", "— تحركات صعودية متواصلة عند تقلب معتدل: يختبر ما إذا كانت ضوابط المخاطر تعيق النظام دون داعٍ أثناء صعود حقيقي.", "— movimentos ascendentes sustentados com volatilidade moderada: testa se os controlos de risco travam desnecessariamente o sistema durante uma subida genuína.", "— mouvements haussiers soutenus à volatilité modérée : teste si les contrôles de risque freinent inutilement le système pendant une vraie hausse.", "— устойчивое движение вверх при умеренной волатильности: проверяет, не сдерживают ли риск-контроли систему без нужды во время настоящего ралли."],

    // --- Council ---
    "Data source": ["数据来源", "Fuente de datos", "डेटा स्रोत", "مصدر البيانات", "Fonte de dados", "Source de données", "Источник данных"],
    "derivatives": ["衍生品", "derivados", "डेरिवेटिव", "المشتقات", "derivados", "dérivés", "деривативы"],
    "liquidity": ["流动性", "liquidez", "लिक्विडिटी", "السيولة", "liquidez", "liquidité", "ликвидность"],
    "funding": ["资金费", "financiación", "फंडिंग", "التمويل", "financiamento", "financement", "фандинг"],
    "orderflow": ["订单流", "flujo de órdenes", "ऑर्डरफ़्लो", "تدفق الأوامر", "fluxo de ordens", "flux d'ordres", "поток ордеров"],
    "regime": ["市场格局", "régimen", "रिजीम", "النظام", "regime", "régime", "режим"],
    "sentiment": ["情绪", "sentimiento", "भावना", "المعنويات", "sentimento", "sentiment", "настроения"],
    "technical": ["技术面", "técnico", "तकनीकी", "فني", "técnico", "technique", "технический"],
    "neutral": ["中性", "neutral", "तटस्थ", "محايد", "neutro", "neutre", "нейтрально"],
    "onchain": ["链上", "onchain", "ऑनचेन", "على السلسلة", "onchain", "onchain", "ончейн"],
    "Looks at": ["关注", "Observa", "देखता है", "ينظر إلى", "Observa", "Examine", "Смотрит на"],
    "Not enough price history yet.": ["价格历史尚不足。", "Aún no hay suficiente historial de precios.", "अभी पर्याप्त मूल्य इतिहास नहीं।", "لا يوجد سجل أسعار كافٍ بعد.", "Ainda não há histórico de preços suficiente.", "Pas encore assez d'historique de prix.", "Пока недостаточно истории цен."],
    "Trend, momentum and mean reversion from real Binance candles": ["来自币安真实K线的趋势、动量与均值回归", "Tendencia, momentum y reversión a la media de velas reales de Binance", "असली बायनेंस कैंडल से ट्रेंड, मोमेंटम और मीन रिवर्ज़न", "الاتجاه والزخم والعودة إلى المتوسط من شموع بينانس الحقيقية", "Tendência, momentum e reversão à média de velas reais da Binance", "Tendance, momentum et retour à la moyenne à partir de vraies bougies Binance", "Тренд, импульс и возврат к среднему по реальным свечам Binance"],
    "Live order book imbalance and aggressive trade pressure": ["实时订单簿失衡与激进成交压力", "Desequilibrio del libro de órdenes en vivo y presión de operaciones agresivas", "लाइव ऑर्डर बुक असंतुलन और आक्रामक ट्रेड दबाव", "اختلال دفتر الأوامر الحي وضغط التداول العدواني", "Desequilíbrio do livro de ordens ao vivo e pressão de negociação agressiva", "Déséquilibre du carnet d'ordres en direct et pression d'échanges agressifs", "Дисбаланс живого стакана и агрессивное торговое давление"],
    "Funding, open interest and crowd positioning from Binance futures": ["来自币安合约的资金费、未平仓量与群体持仓", "Financiación, interés abierto y posicionamiento de la multitud de futuros de Binance", "बायनेंस फ्यूचर्स से फंडिंग, ओपन इंटरेस्ट और भीड़ पोज़िशनिंग", "التمويل والفوائد المفتوحة وتموضع الجمهور من عقود بينانس الآجلة", "Financiamento, interesse em aberto e posicionamento da multidão dos futuros da Binance", "Financement, positions ouvertes et positionnement de la foule des futures Binance", "Фандинг, открытый интерес и позиционирование толпы из фьючерсов Binance"],
    "Volatility regime, BTC leadership and cross-asset correlation": ["波动格局、BTC 主导地位与跨资产相关性", "Régimen de volatilidad, liderazgo de BTC y correlación entre activos", "अस्थिरता रिजीम, BTC नेतृत्व और क्रॉस-एसेट सहसंबंध", "نظام التقلب وقيادة BTC والارتباط بين الأصول", "Regime de volatilidade, liderança do BTC e correlação entre ativos", "Régime de volatilité, leadership du BTC et corrélation inter-actifs", "Режим волатильности, лидерство BTC и межактивная корреляция"],
    "Real slippage cost, walked through the live book at our size": ["真实滑点成本，按我们的下单量在实时订单簿中逐档计算", "Coste real de deslizamiento, recorrido por el libro en vivo a nuestro tamaño", "असली स्लिपेज लागत, हमारे साइज़ पर लाइव बुक से गणना", "تكلفة انزلاق حقيقية، محسوبة عبر الدفتر الحي بحجمنا", "Custo real de derrapagem, percorrido pelo livro ao vivo no nosso tamanho", "Coût réel de glissement, calculé à travers le carnet en direct à notre taille", "Реальная стоимость проскальзывания, рассчитанная по живому стакану на наш объём"],
    "· dissent": ["· 异议", "· disenso", "· असहमति", "· اعتراض", "· dissidência", "· dissidence", "· несогласие"],

    // --- Payments ---
    "Each carries the ledger hash of the reasoning that authorised it, and a real transaction link.": ["每一笔都带有授权该操作的推理账本哈希，以及真实的交易链接。", "Cada uno lleva el hash del libro del razonamiento que lo autorizó y un enlace de transacción real.", "प्रत्येक में उसे अधिकृत करने वाले तर्क का बहीखाता हैश और एक असली लेन-देन लिंक होता है।", "كل عملية تحمل بصمة الدفتر للاستدلال الذي أذن بها، ورابط معاملة حقيقي.", "Cada um carrega o hash do livro-razão do raciocínio que o autorizou e um link de transação real.", "Chacun porte le hash du registre du raisonnement qui l'a autorisé, et un vrai lien de transaction.", "Каждый несёт хеш реестра рассуждения, санкционировавшего его, и реальную ссылку на транзакцию."],
    "Nothing is payable until you add a counterparty with a real address. Fails closed by design.": ["在你添加带真实地址的交易对手之前，无法进行任何支付。设计上默认拒绝。", "No se puede pagar nada hasta que añadas una contraparte con una dirección real. Falla en cerrado por diseño.", "जब तक आप असली पते वाला प्रतिपक्ष नहीं जोड़ते, कुछ भी देय नहीं। डिज़ाइन से फेल-क्लोज़्ड।", "لا يمكن دفع أي شيء حتى تضيف طرفًا مقابلًا بعنوان حقيقي. يفشل مغلقًا بحكم التصميم.", "Nada é pagável até adicionar uma contraparte com um endereço real. Falha fechado por design.", "Aucun paiement possible tant que vous n'ajoutez pas une contrepartie avec une adresse réelle. Échoue en position fermée par conception.", "Оплата невозможна, пока вы не добавите контрагента с реальным адресом. По умолчанию отказ (fail-closed)."],
    "No counterparties yet": ["暂无交易对手", "Aún no hay contrapartes", "अभी कोई प्रतिपक्ष नहीं", "لا أطراف مقابلة بعد", "Ainda sem contrapartes", "Aucune contrepartie pour l'instant", "Пока нет контрагентов"],
    "No payments made yet": ["暂无支付记录", "Aún no se han hecho pagos", "अभी कोई भुगतान नहीं", "لا مدفوعات بعد", "Ainda sem pagamentos", "Aucun paiement pour l'instant", "Пока нет платежей"],
    "Network reachable": ["网络可达", "Red accesible", "नेटवर्क पहुँच-योग्य", "الشبكة قابلة للوصول", "Rede acessível", "Réseau accessible", "Сеть доступна"],
    "USDC contract verified": ["USDC 合约已验证", "Contrato USDC verificado", "USDC कॉन्ट्रैक्ट सत्यापित", "تم التحقق من عقد USDC", "Contrato USDC verificado", "Contrat USDC vérifié", "Контракт USDC проверен"],
    "Test ETH balance": ["测试 ETH 余额", "Probar saldo de ETH", "ETH बैलेंस टेस्ट करें", "اختبار رصيد ETH", "Testar saldo de ETH", "Tester le solde ETH", "Проверить баланс ETH"],
    "Test USDC balance": ["测试 USDC 余额", "Probar saldo de USDC", "USDC बैलेंस टेस्ट करें", "اختبار رصيد USDC", "Testar saldo de USDC", "Tester le solde USDC", "Проверить баланс USDC"],
    "Fund it (free, one-time) from a faucet:": ["通过水龙头充值（免费，一次性）：", "Fináncialo (gratis, una vez) desde un faucet:", "इसे एक फ़ॉसेट से फंड करें (मुफ़्त, एक बार):", "موّلها (مجانًا، لمرة واحدة) من صنبور:", "Financie-o (grátis, uma vez) a partir de uma torneira:", "Approvisionnez-le (gratuit, une fois) depuis un faucet :", "Пополните (бесплатно, разово) из крана:"],
    "view on explorer": ["在区块浏览器中查看", "ver en el explorador", "एक्सप्लोरर पर देखें", "عرض في المستكشف", "ver no explorador", "voir sur l'explorateur", "смотреть в обозревателе"],

    // --- Onchain empty states ---
    "No actions yet": ["暂无操作", "Aún no hay acciones", "अभी कोई कार्रवाई नहीं", "لا إجراءات بعد", "Ainda sem ações", "Aucune action pour l'instant", "Пока нет действий"],
    "No active stakes.": ["暂无进行中的质押。", "Sin staking activo.", "कोई सक्रिय स्टेक नहीं।", "لا رهانات نشطة.", "Sem staking ativo.", "Aucun staking actif.", "Нет активных стейков."],
    "No swapped holdings yet.": ["暂无兑换持仓。", "Aún no hay activos intercambiados.", "अभी कोई स्वैप की गई होल्डिंग नहीं।", "لا أصول مبادَلة بعد.", "Ainda sem ativos trocados.", "Aucun actif échangé pour l'instant.", "Пока нет обменянных активов."],
    "Stake, swap or pay above — each one is signed to the ledger.": ["在上方质押、兑换或支付——每一笔都会签名写入账本。", "Haz staking, intercambia o paga arriba: cada acción se firma en el libro mayor.", "ऊपर स्टेक, स्वैप या भुगतान करें — प्रत्येक बहीखाते में हस्ताक्षरित होता है।", "اربط أو بادل أو ادفع أعلاه — كل عملية تُوقّع في الدفتر.", "Faça staking, troque ou pague acima — cada ação é assinada no livro-razão.", "Stakez, échangez ou payez ci-dessus — chaque action est signée au registre.", "Стейкайте, обменивайте или платите выше — каждое действие подписывается в реестр."],

    // --- Sentinel ---
    "Hedges triggered": ["已触发对冲", "Coberturas activadas", "हेज ट्रिगर हुए", "تم تفعيل التحوطات", "Coberturas acionadas", "Couvertures déclenchées", "Хеджи сработали"],
    "Intents vetoed": ["被否决的意图", "Intenciones vetadas", "वीटो किए गए इंटेंट", "نوايا مرفوضة", "Intenções vetadas", "Intentions rejetées", "Отклонённые намерения"],
    "Elevated at": ["升高于", "Elevado en", "पर बढ़ा", "مرتفع عند", "Elevado em", "Élevé à", "Повышен при"],
    "Quarantine at": ["隔离于", "Cuarentena en", "पर क्वारंटीन", "حجر عند", "Quarentena em", "Quarantaine à", "Карантин при"],
    "No elevated risk factors detected": ["未检测到升高的风险因素", "No se detectaron factores de riesgo elevados", "कोई बढ़ा जोखिम कारक नहीं मिला", "لم تُكتشف عوامل خطر مرتفعة", "Nenhum fator de risco elevado detetado", "Aucun facteur de risque élevé détecté", "Повышенных факторов риска не обнаружено"],
    "Nothing has needed defending": ["尚无需防御的情况", "Nada ha necesitado defensa", "अभी तक कुछ भी बचाव की ज़रूरत नहीं", "لا شيء احتاج للدفاع", "Nada precisou de defesa", "Rien n'a nécessité de défense", "Пока ничего не потребовало защиты"],
    "Stops, hedges and quarantines appear here. Use the drill controls above to force one.": ["止损、对冲与隔离会显示在此处。使用上方的演练控件可强制触发一次。", "Stops, coberturas y cuarentenas aparecen aquí. Usa los controles de prueba de arriba para forzar uno.", "स्टॉप, हेज और क्वारंटीन यहाँ दिखते हैं। किसी को बलपूर्वक ट्रिगर करने के लिए ऊपर के ड्रिल नियंत्रण उपयोग करें।", "تظهر هنا أوامر الإيقاف والتحوطات والحجر. استخدم أدوات التدريب أعلاه لفرض واحدة.", "Stops, coberturas e quarentenas aparecem aqui. Use os controlos de teste acima para forçar um.", "Les stops, couvertures et quarantaines apparaissent ici. Utilisez les contrôles de test ci-dessus pour en forcer un.", "Стопы, хеджи и карантины появляются здесь. Используйте элементы проверки выше, чтобы вызвать вручную."],
    "Scored every tick from real price velocity, volume and volatility — before any agent is allowed to speak": ["在任何智能体被允许发言之前，就根据真实价格速度、成交量与波动率对每个跳动评分", "Puntúa cada tick a partir de la velocidad real del precio, el volumen y la volatilidad, antes de que cualquier agente pueda hablar", "किसी भी एजेंट को बोलने की अनुमति से पहले असली मूल्य वेग, वॉल्यूम और अस्थिरता से हर टिक को स्कोर किया", "يُقيّم كل نبضة من سرعة السعر الحقيقية والحجم والتقلب — قبل أن يُسمح لأي وكيل بالكلام", "Pontua cada tick a partir da velocidade real do preço, volume e volatilidade — antes de qualquer agente poder falar", "Note chaque tick à partir de la vélocité réelle du prix, du volume et de la volatilité — avant qu'un agent ne puisse parler", "Оценивает каждый тик по реальной скорости цены, объёму и волатильности — прежде чем любому агенту позволено высказаться"],
    "normal": ["正常", "normal", "सामान्य", "طبيعي", "normal", "normal", "нормально"],

    // --- Yield ---
    "Daily budget used": ["已用每日预算", "Presupuesto diario usado", "उपयोग किया दैनिक बजट", "الميزانية اليومية المستخدمة", "Orçamento diário usado", "Budget quotidien utilisé", "Использованный дневной бюджет"],
    "Deployed": ["已部署", "Desplegado", "तैनात", "منشور", "Implantado", "Déployé", "Развёрнуто"],
    "Earned": ["已赚取", "Ganado", "अर्जित", "مكتسب", "Ganho", "Gagné", "Заработано"],
    "Kept as dry powder": ["保留为备用资金", "Mantenido como pólvora seca", "ड्राई पाउडर के रूप में रखा", "محتفظ به كسيولة احتياطية", "Mantido como pólvora seca", "Conservé comme poudre sèche", "Оставлено как сухой порох"],
    "Lockup": ["锁定期", "Bloqueo", "लॉकअप", "فترة القفل", "Bloqueio", "Blocage", "Период блокировки"],
    "Minimum to move": ["最小变动量", "Mínimo para mover", "हिलाने के लिए न्यूनतम", "الحد الأدنى للتحريك", "Mínimo para mover", "Minimum pour bouger", "Минимум для перемещения"],
    "Remaining today": ["今日剩余", "Restante hoy", "आज शेष", "المتبقي اليوم", "Restante hoje", "Restant aujourd'hui", "Осталось сегодня"],
    "Requests paid": ["已付费请求", "Solicitudes pagadas", "भुगतान किए अनुरोध", "الطلبات المدفوعة", "Pedidos pagos", "Requêtes payées", "Оплаченные запросы"],
    "Risk-adjusted": ["风险调整后", "Ajustado al riesgo", "जोखिम-समायोजित", "معدّل حسب المخاطرة", "Ajustado ao risco", "Ajusté au risque", "С поправкой на риск"],
    "Venue": ["场所", "Plataforma", "वेन्यू", "المنصة", "Plataforma", "Plateforme", "Площадка"],

    // --- Ledger ---
    "Witness": ["见证", "Testigo", "साक्षी", "شاهد", "Testemunha", "Témoin", "Свидетель"],
    "Ledger head anchored": ["账本头已锚定", "Cabeza del libro anclada", "बहीखाता हेड एंकर किया", "تم تثبيت رأس الدفتر", "Cabeça do livro-razão ancorada", "Tête du registre ancrée", "Голова реестра закреплена"],
    "Each checkpoint binds the ledger head to Binance's server clock and market price.": ["每个检查点将账本头绑定到币安的服务器时钟与市场价格。", "Cada checkpoint vincula la cabeza del libro al reloj del servidor de Binance y al precio de mercado.", "प्रत्येक चेकपॉइंट बहीखाता हेड को बायनेंस के सर्वर घड़ी और बाज़ार मूल्य से बाँधता है।", "يربط كل نقطة تفتيش رأس الدفتر بساعة خادم بينانس وسعر السوق.", "Cada checkpoint vincula a cabeça do livro-razão ao relógio do servidor da Binance e ao preço de mercado.", "Chaque point de contrôle lie la tête du registre à l'horloge serveur de Binance et au prix du marché.", "Каждая контрольная точка привязывает голову реестра к серверным часам Binance и рыночной цене."],
    "to re-derive every hash and signature from genesis.": ["以从创世起重新推导每一个哈希与签名。", "para volver a derivar cada hash y firma desde el génesis.", "जेनेसिस से हर हैश और हस्ताक्षर पुनः निकालने के लिए।", "لإعادة اشتقاق كل بصمة وتوقيع من البداية.", "para re-derivar cada hash e assinatura desde a génese.", "pour recalculer chaque hash et signature depuis la genèse.", "чтобы заново вывести каждый хеш и подпись от генезиса."],

    // --- Cockpit leftovers ---
    "Spread": ["价差", "Spread", "स्प्रेड", "الفارق السعري", "Spread", "Spread", "Спред"],
    "Engine started in paper mode.": ["引擎已在模拟模式下启动。", "Motor iniciado en modo simulado.", "इंजन पेपर मोड में शुरू हुआ।", "بدأ المحرك في الوضع التجريبي.", "Motor iniciado em modo de papel.", "Moteur démarré en mode papier.", "Движок запущен в бумажном режиме."],
    "Every decision — taken or refused — will appear here with its ledger hash.": ["每一个决策——无论执行还是拒绝——都会连同其账本哈希显示在此处。", "Cada decisión —tomada o rechazada— aparecerá aquí con su hash del libro.", "प्रत्येक निर्णय — लिया गया या अस्वीकृत — यहाँ अपने बहीखाता हैश के साथ दिखेगा।", "كل قرار — منفَّذ أو مرفوض — سيظهر هنا مع بصمة الدفتر الخاصة به.", "Cada decisão — tomada ou recusada — aparecerá aqui com o seu hash do livro-razão.", "Chaque décision — prise ou refusée — apparaîtra ici avec son hash de registre.", "Каждое решение — принятое или отклонённое — появится здесь со своим хешем реестра."],

    // --- Track record / Council detail ---
    "How to read this": ["如何阅读", "Cómo leer esto", "इसे कैसे पढ़ें", "كيف تقرأ هذا", "Como ler isto", "Comment lire ceci", "Как это читать"],
    "Calls graded so far": ["迄今已评分的判断", "Decisiones calificadas hasta ahora", "अब तक आँके गए कॉल", "التوقعات المُقيَّمة حتى الآن", "Decisões avaliadas até agora", "Appels notés jusqu'ici", "Оценённых прогнозов на данный момент"],
    "Calls awaiting grade": ["待评分的判断", "Decisiones pendientes de calificar", "आँकने के लिए लंबित कॉल", "توقعات بانتظار التقييم", "Decisões a aguardar avaliação", "Appels en attente de note", "Прогнозы, ожидающие оценки"],
    "Grading horizon": ["评分周期", "Horizonte de calificación", "ग्रेडिंग क्षितिज", "أفق التقييم", "Horizonte de avaliação", "Horizon de notation", "Горизонт оценки"],
    "Brier score": ["Brier 分数", "Puntuación de Brier", "Brier स्कोर", "درجة Brier", "Pontuação de Brier", "Score de Brier", "Оценка Брайера"],
    "Hit rate": ["命中率", "Tasa de acierto", "हिट दर", "معدل الإصابة", "Taxa de acerto", "Taux de réussite", "Доля попаданий"],
    "Buy & hold": ["买入持有", "Comprar y mantener", "बाय एंड होल्ड", "الشراء والاحتفاظ", "Comprar e manter", "Acheter et conserver", "Купить и держать"],
    "Buy & hold benchmark": ["买入持有基准", "Referencia de comprar y mantener", "बाय एंड होल्ड बेंचमार्क", "مرجع الشراء والاحتفاظ", "Referência de comprar e manter", "Référence acheter-conserver", "Бенчмарк «купить и держать»"],
    "Final equity": ["最终净值", "Patrimonio final", "अंतिम इक्विटी", "حقوق الملكية النهائية", "Património final", "Capitaux propres finaux", "Итоговый капитал"],
    "What each analyst saw": ["每位分析师所见", "Lo que vio cada analista", "प्रत्येक विश्लेषक ने क्या देखा", "ما رآه كل محلل", "O que cada analista viu", "Ce que chaque analyste a vu", "Что видел каждый аналитик"],
    "What the analysts see right now": ["分析师此刻所见", "Lo que ven los analistas ahora mismo", "विश्लेषक अभी क्या देख रहे हैं", "ما يراه المحللون الآن", "O que os analistas veem agora", "Ce que voient les analystes en ce moment", "Что аналитики видят сейчас"],
    "Full reasoning and rules applied": ["完整推理与已应用规则", "Razonamiento completo y reglas aplicadas", "पूर्ण तर्क और लागू नियम", "الاستدلال الكامل والقواعد المطبَّقة", "Raciocínio completo e regras aplicadas", "Raisonnement complet et règles appliquées", "Полное обоснование и применённые правила"],
    "No analyst signals were recorded for this decision.": ["该决策未记录任何分析师信号。", "No se registraron señales de analistas para esta decisión.", "इस निर्णय के लिए कोई विश्लेषक संकेत दर्ज नहीं हुआ।", "لم تُسجَّل إشارات محللين لهذا القرار.", "Não foram registados sinais de analistas para esta decisão.", "Aucun signal d'analyste n'a été enregistré pour cette décision.", "Для этого решения не записано сигналов аналитиков."],
    "What the Constitution said, and the exact MCP call": ["章程的判定，以及确切的 MCP 调用", "Lo que dijo la Constitución y la llamada MCP exacta", "संविधान ने क्या कहा, और सटीक MCP कॉल", "ما قاله الدستور، ونداء MCP الدقيق", "O que a Constituição disse e a chamada MCP exata", "Ce que la Constitution a dit, et l'appel MCP exact", "Что сказала Конституция, и точный вызов MCP"],
    "Decision receipts": ["决策回执", "Recibos de decisión", "निर्णय रसीदें", "إيصالات القرار", "Recibos de decisão", "Reçus de décision", "Квитанции решений"],
    "Rules applied": ["已应用规则", "Reglas aplicadas", "लागू नियम", "القواعد المطبَّقة", "Regras aplicadas", "Règles appliquées", "Применённые правила"],
    "Justification": ["理由", "Justificación", "औचित्य", "التبرير", "Justificação", "Justification", "Обоснование"],
    "Proof": ["证明", "Prueba", "प्रमाण", "إثبات", "Prova", "Preuve", "Доказательство"],
    "Position sizing": ["头寸规模", "Dimensionamiento de posición", "पोज़िशन साइज़िंग", "تحديد حجم المركز", "Dimensionamento da posição", "Dimensionnement de position", "Размер позиции"],
    "Edge": ["优势", "Ventaja", "एज", "أفضلية", "Vantagem", "Avantage", "Преимущество"],

    // --- Events / macro ---
    "No headlines retrieved.": ["未获取到头条。", "No se recuperaron titulares.", "कोई सुर्खियाँ प्राप्त नहीं हुईं।", "لم يتم جلب أي عناوين.", "Nenhuma manchete obtida.", "Aucun titre récupéré.", "Заголовки не получены."],
    "Scheduled events ahead": ["即将到来的排定事件", "Eventos programados próximos", "आगामी निर्धारित घटनाएँ", "أحداث مجدولة قادمة", "Eventos agendados à frente", "Événements programmés à venir", "Предстоящие запланированные события"],
    "Full US Treasury curve": ["完整美国国债收益率曲线", "Curva completa del Tesoro de EE. UU.", "पूर्ण यूएस ट्रेज़री कर्व", "منحنى الخزانة الأمريكية الكامل", "Curva completa do Tesouro dos EUA", "Courbe complète du Trésor américain", "Полная кривая казначейства США"],
    "Market is pricing": ["市场正在定价", "El mercado está descontando", "बाज़ार मूल्यांकन कर रहा है", "السوق يُسعّر", "O mercado está a precificar", "Le marché intègre", "Рынок закладывает"],
    "Next FOMC decision": ["下次 FOMC 决议", "Próxima decisión del FOMC", "अगला FOMC निर्णय", "قرار FOMC القادم", "Próxima decisão do FOMC", "Prochaine décision du FOMC", "Следующее решение FOMC"],
    "US 10-year": ["美国10年期", "EE. UU. a 10 años", "यूएस 10-वर्ष", "سندات أمريكية 10 سنوات", "EUA 10 anos", "US 10 ans", "США 10 лет"],
    "US 2-year": ["美国2年期", "EE. UU. a 2 años", "यूएस 2-वर्ष", "سندات أمريكية سنتان", "EUA 2 anos", "US 2 ans", "США 2 года"],

    // --- Coins / Pairs ---
    "Could not load coins": ["无法加载币种", "No se pudieron cargar las monedas", "सिक्के लोड नहीं हो सके", "تعذّر تحميل العملات", "Não foi possível carregar as moedas", "Impossible de charger les cryptos", "Не удалось загрузить монеты"],
    "No coin matched": ["无匹配的币种", "Ninguna moneda coincidió", "कोई सिक्का मेल नहीं खाया", "لا عملة مطابقة", "Nenhuma moeda correspondeu", "Aucune crypto ne correspond", "Нет подходящих монет"],
    "No matching pair": ["无匹配的交易对", "Ningún par coincidente", "कोई मेल खाता जोड़ा नहीं", "لا زوج مطابق", "Nenhum par correspondente", "Aucune paire correspondante", "Нет подходящих пар"],
    "Volume, all pairs": ["成交量（所有交易对）", "Volumen, todos los pares", "वॉल्यूम, सभी जोड़े", "الحجم، كل الأزواج", "Volume, todos os pares", "Volume, toutes paires", "Объём, все пары"],
    "Book depth shown": ["显示的订单簿深度", "Profundidad del libro mostrada", "दिखाई गई बुक गहराई", "عمق الدفتر المعروض", "Profundidade do livro mostrada", "Profondeur du carnet affichée", "Показанная глубина стакана"],

    // --- toasts / status ---
    "Engine running.": ["引擎运行中。", "Motor en marcha.", "इंजन चल रहा है।", "المحرك يعمل.", "Motor em funcionamento.", "Moteur en marche.", "Движок работает."],
    "Engine paused. Open positions untouched.": ["引擎已暂停。未平仓头寸保持不变。", "Motor en pausa. Posiciones abiertas intactas.", "इंजन रुका। खुली पोज़िशन अछूती।", "تم إيقاف المحرك مؤقتًا. المراكز المفتوحة دون تغيير.", "Motor em pausa. Posições abertas intactas.", "Moteur en pause. Positions ouvertes inchangées.", "Движок приостановлен. Открытые позиции не тронуты."],
    "Autonomous trading enabled.": ["已启用自主交易。", "Trading autónomo activado.", "स्वायत्त ट्रेडिंग सक्षम।", "تم تفعيل التداول الذاتي.", "Trading autónomo ativado.", "Trading autonome activé.", "Автономная торговля включена."],
    "Autonomous trading disabled.": ["已禁用自主交易。", "Trading autónomo desactivado.", "स्वायत्त ट्रेडिंग अक्षम।", "تم تعطيل التداول الذاتي.", "Trading autónomo desativado.", "Trading autonome désactivé.", "Автономная торговля отключена."],
    "Headlines refreshed.": ["头条已刷新。", "Titulares actualizados.", "सुर्खियाँ रिफ़्रेश हुईं।", "تم تحديث العناوين.", "Manchetes atualizadas.", "Titres actualisés.", "Заголовки обновлены."],
    "Quarantine cleared.": ["隔离已解除。", "Cuarentena eliminada.", "क्वारंटीन साफ़।", "تم إزالة الحجر.", "Quarentena removida.", "Quarantaine levée.", "Карантин снят."],
    "Kill switch released.": ["熔断开关已释放。", "Interruptor de emergencia liberado.", "किल स्विच रिलीज़।", "تم تحرير مفتاح الإيقاف.", "Kill switch libertado.", "Coupe-circuit relâché.", "Аварийный выключатель отпущен."],
    "Kill switch engaged. No new risk will be taken.": ["熔断开关已启用。不会再承担任何新风险。", "Interruptor de emergencia activado. No se asumirá ningún riesgo nuevo.", "किल स्विच सक्रिय। कोई नया जोखिम नहीं लिया जाएगा।", "تم تفعيل مفتاح الإيقاف. لن تُتَّخذ أي مخاطرة جديدة.", "Kill switch acionado. Nenhum risco novo será assumido.", "Coupe-circuit activé. Aucun nouveau risque ne sera pris.", "Аварийный выключатель включён. Новый риск не будет взят."],
    "Kill switch engaged": ["熔断开关已启用", "Interruptor de emergencia activado", "किल स्विच सक्रिय", "تم تفعيل مفتاح الإيقاف", "Kill switch acionado", "Coupe-circuit activé", "Аварийный выключатель включён"],
    "Client ID saved.": ["客户端 ID 已保存。", "ID de cliente guardado.", "क्लाइंट ID सहेजा गया।", "تم حفظ معرّف العميل.", "ID de cliente guardado.", "ID client enregistré.", "Client ID сохранён."],
    "Client ID cleared.": ["客户端 ID 已清除。", "ID de cliente borrado.", "क्लाइंट ID साफ़।", "تم مسح معرّف العميل.", "ID de cliente limpo.", "ID client effacé.", "Client ID очищен."],
    "Counterparty added.": ["交易对手已添加。", "Contraparte añadida.", "प्रतिपक्ष जोड़ा गया।", "تمت إضافة الطرف المقابل.", "Contraparte adicionada.", "Contrepartie ajoutée.", "Контрагент добавлен."],
    "Constitution reloaded and the change recorded.": ["章程已重新加载，变更已记录。", "Constitución recargada y el cambio registrado.", "संविधान पुनः लोड और परिवर्तन दर्ज।", "تم إعادة تحميل الدستور وتسجيل التغيير.", "Constituição recarregada e a alteração registada.", "Constitution rechargée et changement enregistré.", "Конституция перезагружена, изменение записано."],
    "Disconnected and session cleared.": ["已断开连接并清除会话。", "Desconectado y sesión borrada.", "डिस्कनेक्ट और सत्र साफ़।", "تم قطع الاتصال ومسح الجلسة.", "Desconectado e sessão limpa.", "Déconnecté et session effacée.", "Отключено, сессия очищена."],
    "All drills passed.": ["所有演练均已通过。", "Todas las pruebas superadas.", "सभी ड्रिल पास।", "نجحت كل الاختبارات.", "Todos os testes passaram.", "Tous les tests réussis.", "Все проверки пройдены."],
    "Trade cancelled.": ["交易已取消。", "Operación cancelada.", "ट्रेड रद्द।", "تم إلغاء الصفقة.", "Operação cancelada.", "Transaction annulée.", "Сделка отменена."],
    "Trade was not placed.": ["交易未提交。", "La operación no se realizó.", "ट्रेड नहीं हुआ।", "لم تُنفَّذ الصفقة.", "A operação não foi feita.", "La transaction n'a pas été passée.", "Сделка не размещена."],
    "Approved and executed.": ["已批准并执行。", "Aprobado y ejecutado.", "स्वीकृत और निष्पादित।", "تمت الموافقة والتنفيذ.", "Aprovado e executado.", "Approuvé et exécuté.", "Одобрено и исполнено."],
    "Chain verified intact.": ["链验证完整无损。", "Cadena verificada intacta.", "श्रृंखला अक्षुण्ण सत्यापित।", "تم التحقق من سلامة السلسلة.", "Cadeia verificada intacta.", "Chaîne vérifiée intacte.", "Цепочка проверена, цела."],
    "Chain verification failed.": ["链验证失败。", "Verificación de cadena fallida.", "श्रृंखला सत्यापन विफल।", "فشل التحقق من السلسلة.", "Verificação da cadeia falhou.", "Échec de la vérification de la chaîne.", "Проверка цепочки не удалась."],
    "Anchor verification failed.": ["锚点验证失败。", "Verificación de anclaje fallida.", "एंकर सत्यापन विफल।", "فشل التحقق من التثبيت.", "Verificação de âncora falhou.", "Échec de la vérification d'ancrage.", "Проверка якоря не удалась."],
    "Ledger anchored.": ["账本已锚定。", "Libro anclado.", "बहीखाता एंकर।", "تم تثبيت الدفتر.", "Livro-razão ancorado.", "Registre ancré.", "Реестр закреплён."],
    "Funding is negative — shorts are paying, so a rally would squeeze them.": ["资金费为负——空头在付费，因此上涨会逼空他们。", "La financiación es negativa: los cortos pagan, así que un repunte los exprimiría.", "फंडिंग नकारात्मक — शॉर्ट भुगतान कर रहे हैं, इसलिए तेज़ी उन्हें स्क्वीज़ करेगी।", "التمويل سالب — البائعون على المكشوف يدفعون، لذا سيضغط الصعود عليهم.", "O financiamento é negativo — os vendidos estão a pagar, por isso uma subida iria espremê-los.", "Le financement est négatif — les vendeurs paient, une hausse les squeezerait donc.", "Фандинг отрицательный — шорты платят, поэтому рост их сожмёт."],
    "Funding is positive — the crowd is paying to be long, which leaves room for a squeeze.": ["资金费为正——市场在付费做多，这为逼空留下了空间。", "La financiación es positiva: la multitud paga por estar en largo, lo que deja margen para un squeeze.", "फंडिंग सकारात्मक — भीड़ लॉन्ग रहने के लिए भुगतान कर रही है, जिससे स्क्वीज़ की गुंजाइश बचती है।", "التمويل موجب — الجمهور يدفع للبقاء في مراكز شراء، مما يترك مجالًا للضغط.", "O financiamento é positivo — a multidão está a pagar para estar comprada, o que deixa espaço para um squeeze.", "Le financement est positif — la foule paie pour être acheteuse, ce qui laisse place à un squeeze.", "Фандинг положительный — толпа платит за лонг, что оставляет место для сквиза."],
    "Conditions are within tolerance.": ["各项指标均在容许范围内。", "Las condiciones están dentro de la tolerancia.", "स्थितियाँ सहनशीलता के भीतर हैं।", "الظروف ضمن الحدود المسموح بها.", "As condições estão dentro da tolerância.", "Les conditions sont dans la tolérance.", "Условия в пределах допустимого."],
    "Critical at": ["危急阈值", "Crítico en", "गंभीर पर", "حرج عند", "Crítico em", "Critique à", "Критично при"],
    "conviction": ["高信念", "convicción", "कन्विक्शन", "اقتناع", "convicção", "conviction", "убеждённость"],
    "Cost / call": ["每次调用成本", "Coste / llamada", "लागत / कॉल", "التكلفة / نداء", "Custo / chamada", "Coût / appel", "Стоимость / вызов"],
    "free": ["免费", "gratis", "मुफ़्त", "مجاني", "grátis", "gratuit", "бесплатно"],

    // --- loading / progress ---
    "Analyzing live data…": ["正在分析实时数据…", "Analizando datos en vivo…", "लाइव डेटा विश्लेषण…", "تحليل البيانات الحية…", "A analisar dados ao vivo…", "Analyse des données en direct…", "Анализ данных в реальном времени…"],
    "Analyzing…": ["分析中…", "Analizando…", "विश्लेषण…", "جارٍ التحليل…", "A analisar…", "Analyse…", "Анализ…"],
    "Applying…": ["应用中…", "Aplicando…", "लागू हो रहा है…", "جارٍ التطبيق…", "A aplicar…", "Application…", "Применение…"],
    "checking…": ["检查中…", "comprobando…", "जाँच…", "جارٍ التحقق…", "a verificar…", "vérification…", "проверка…"],
    "Checking…": ["检查中…", "Comprobando…", "जाँच…", "جارٍ التحقق…", "A verificar…", "Vérification…", "Проверка…"],
    "Closing…": ["关闭中…", "Cerrando…", "बंद हो रहा है…", "جارٍ الإغلاق…", "A fechar…", "Fermeture…", "Закрытие…"],
    "Connecting…": ["连接中…", "Conectando…", "कनेक्ट हो रहा है…", "جارٍ الاتصال…", "A ligar…", "Connexion…", "Подключение…"],
    "Looking up…": ["查询中…", "Buscando…", "देख रहे हैं…", "جارٍ البحث…", "A procurar…", "Recherche…", "Поиск…"],
    "Placing…": ["下单中…", "Colocando…", "लगाया जा रहा है…", "جارٍ التنفيذ…", "A colocar…", "Placement…", "Размещение…"],
    "Refreshing…": ["刷新中…", "Actualizando…", "रिफ़्रेश हो रहा है…", "جارٍ التحديث…", "A atualizar…", "Actualisation…", "Обновление…"],
    "Scanning themes…": ["扫描主题中…", "Escaneando temas…", "थीम स्कैन…", "جارٍ فحص السرديات…", "A analisar temas…", "Analyse des thèmes…", "Сканирование тем…"],
    "Downloading real candles from Binance…": ["正在从币安下载真实K线…", "Descargando velas reales de Binance…", "बायनेंस से असली कैंडल डाउनलोड…", "تنزيل الشموع الحقيقية من بينانس…", "A descarregar velas reais da Binance…", "Téléchargement des vraies bougies depuis Binance…", "Загрузка реальных свечей с Binance…"],
    "Loading live pairs from Binance…": ["正在从币安加载实时交易对…", "Cargando pares en vivo de Binance…", "बायनेंस से लाइव जोड़े लोड…", "تحميل الأزواج الحية من بينانس…", "A carregar pares ao vivo da Binance…", "Chargement des paires en direct depuis Binance…", "Загрузка активных пар с Binance…"],
    "Re-deriving every hash from genesis…": ["正在从创世重新推导每个哈希…", "Recalculando cada hash desde el génesis…", "जेनेसिस से हर हैश पुनः निकालना…", "إعادة اشتقاق كل بصمة من البداية…", "A re-derivar cada hash desde a génese…", "Recalcul de chaque hash depuis la genèse…", "Пересчёт каждого хеша от генезиса…"],

    // --- labels ---
    "24h range": ["24小时区间", "Rango 24h", "24घं रेंज", "نطاق 24 ساعة", "Intervalo 24h", "Plage 24h", "Диапазон за 24ч"],
    "24h volume": ["24小时成交量", "Volumen 24h", "24घं वॉल्यूम", "حجم 24 ساعة", "Volume 24h", "Volume 24h", "Объём за 24ч"],
    "Candle": ["K线", "Vela", "कैंडल", "شمعة", "Vela", "Bougie", "Свеча"],
    "Bars": ["K线数", "Barras", "बार", "شموع", "Barras", "Barres", "Бары"],
    "Action": ["操作", "Acción", "क्रिया", "إجراء", "Ação", "Action", "Действие"],
    "Analyst": ["分析师", "Analista", "विश्लेषक", "محلل", "Analista", "Analyste", "Аналитик"],
    "Cap per request": ["每次请求上限", "Límite por solicitud", "प्रति अनुरोध सीमा", "الحد لكل طلب", "Limite por pedido", "Plafond par requête", "Лимит на запрос"],
    "Block / slot": ["区块 / 槽位", "Bloque / slot", "ब्लॉक / स्लॉट", "كتلة / خانة", "Bloco / slot", "Bloc / créneau", "Блок / слот"],
    "Head": ["头部", "Cabeza", "हेड", "الرأس", "Cabeça", "Tête", "Голова"],
    "From": ["从", "De", "से", "من", "De", "De", "От"],
    "To": ["到", "A", "तक", "إلى", "Para", "À", "Кому"],
    "Mode": ["模式", "Modo", "मोड", "الوضع", "Modo", "Mode", "Режим"],
    "Setting": ["设置", "Ajuste", "सेटिंग", "إعداد", "Definição", "Réglage", "Настройка"],
    "Stop": ["停止", "Detener", "रोकें", "إيقاف", "Parar", "Arrêter", "Стоп"],
    "Trades": ["交易", "Operaciones", "ट्रेड", "الصفقات", "Operações", "Transactions", "Сделки"],
    "Transactions sent": ["已发送交易", "Transacciones enviadas", "भेजे गए लेन-देन", "المعاملات المرسلة", "Transações enviadas", "Transactions envoyées", "Отправленные транзакции"],
    "Rewards": ["奖励", "Recompensas", "रिवॉर्ड", "المكافآت", "Recompensas", "Récompenses", "Награды"],
    "Long (bought)": ["多头（买入）", "Largo (comprado)", "लॉन्ग (खरीदा)", "شراء (تم الشراء)", "Comprado (long)", "Long (acheté)", "Лонг (куплено)"],
    "quote asset": ["计价资产", "activo de cotización", "कोट एसेट", "أصل التسعير", "ativo de cotação", "actif de cotation", "котируемый актив"],
    "BTC at that moment": ["当时的 BTC", "BTC en ese momento", "उस समय BTC", "BTC في تلك اللحظة", "BTC nesse momento", "BTC à ce moment", "BTC на тот момент"],
    "Orders sent through MCP": ["通过 MCP 发送的订单", "Órdenes enviadas por MCP", "MCP से भेजे ऑर्डर", "أوامر مُرسلة عبر MCP", "Ordens enviadas via MCP", "Ordres envoyés via MCP", "Ордера через MCP"],
    "Tools Binance exposed to this session": ["币安向本会话开放的工具", "Herramientas que Binance expuso a esta sesión", "इस सत्र को बायनेंस द्वारा दिए टूल", "الأدوات التي أتاحها بينانس لهذه الجلسة", "Ferramentas que a Binance expôs a esta sessão", "Outils exposés par Binance à cette session", "Инструменты, открытые Binance этой сессии"],
    "Change model": ["更换模型", "Cambiar modelo", "मॉडल बदलें", "تغيير النموذج", "Mudar modelo", "Changer de modèle", "Сменить модель"],
    "Agentic sub-account": ["智能体子账户", "Subcuenta Agentic", "Agentic सब-अकाउंट", "حساب Agentic الفرعي", "Subconta Agentic", "Sous-compte Agentic", "Субсчёт Agentic"],
    "Binance Agent OS session": ["币安 Agent OS 会话", "Sesión de Binance Agent OS", "बायनेंस Agent OS सत्र", "جلسة Binance Agent OS", "Sessão do Binance Agent OS", "Session Binance Agent OS", "Сессия Binance Agent OS"],
    "Binance OAuth client ID": ["币安 OAuth 客户端 ID", "ID de cliente OAuth de Binance", "बायनेंस OAuth क्लाइंट ID", "معرّف عميل OAuth لبينانس", "ID de cliente OAuth da Binance", "ID client OAuth Binance", "OAuth client ID для Binance"],
    "records re-hashed and signature-checked": ["已重新哈希并校验签名的记录", "registros re-hasheados y con firma verificada", "रिकॉर्ड पुनः हैश और हस्ताक्षर-जाँचे", "سجلات أُعيد تجزئتها والتحقق من توقيعها", "registos re-hasheados e com assinatura verificada", "enregistrements re-hachés et signatures vérifiées", "записи перехешированы и проверены по подписи"],
    "needs your decision": ["需要你的决定", "necesita tu decisión", "आपके निर्णय की ज़रूरत", "يحتاج قرارك", "precisa da sua decisão", "nécessite votre décision", "требует вашего решения"],
    "grounded desk": ["有依据的问询台", "mesa fundamentada", "ग्राउंडेड डेस्क", "مكتب مُستنِد", "mesa fundamentada", "bureau fondé", "обоснованный деск"],
    "P&L (this position)": ["盈亏（本持仓）", "P&L (esta posición)", "P&L (यह पोज़िशन)", "الربح والخسارة (هذا المركز)", "P&L (esta posição)", "P&L (cette position)", "P&L (эта позиция)"],
    "GlassBox — agent console": ["GlassBox — 智能体控制台", "GlassBox — consola del agente", "GlassBox — एजेंट कंसोल", "GlassBox — وحدة تحكم الوكيل", "GlassBox — consola do agente", "GlassBox — console de l'agent", "GlassBox — консоль агента"],

    // --- empty states / statuses ---
    "Capital is idle. The Council opens one when enough analysts agree and the rules allow it.": ["资金处于闲置。当足够多的分析师达成一致且规则允许时，议会才会开仓。", "El capital está inactivo. El Consejo abre una posición cuando suficientes analistas coinciden y las reglas lo permiten.", "पूंजी निष्क्रिय है। जब पर्याप्त विश्लेषक सहमत हों और नियम अनुमति दें, तब परिषद पोज़िशन खोलती है।", "رأس المال خامل. يفتح المجلس مركزًا عندما يتفق عدد كافٍ من المحللين وتسمح القواعد بذلك.", "O capital está parado. O Conselho abre uma posição quando analistas suficientes concordam e as regras permitem.", "Le capital est inactif. Le Conseil ouvre une position lorsque suffisamment d'analystes sont d'accord et que les règles l'autorisent.", "Капитал простаивает. Совет открывает позицию, когда достаточно аналитиков согласны и правила это позволяют."],
    "Every analyst is currently neutral — no directional view on any watched pair.": ["所有分析师目前均为中性——对任何关注的交易对都没有方向性观点。", "Todos los analistas están neutrales ahora mismo: sin visión direccional sobre ningún par vigilado.", "सभी विश्लेषक अभी तटस्थ हैं — किसी भी देखी जा रही जोड़ी पर कोई दिशात्मक दृष्टिकोण नहीं।", "كل المحللين محايدون حاليًا — لا رأي اتجاهي بشأن أي زوج مُراقَب.", "Todos os analistas estão neutros neste momento — sem visão direcional sobre qualquer par vigiado.", "Tous les analystes sont neutres actuellement — aucune vue directionnelle sur les paires suivies.", "Все аналитики сейчас нейтральны — нет направленного взгляда ни по одной отслеживаемой паре."],
    "Every analyst will examine it live.": ["每位分析师都会实时分析它。", "Cada analista lo examinará en vivo.", "प्रत्येक विश्लेषक इसे लाइव जाँचेगा।", "سيفحصه كل محلل مباشرةً.", "Cada analista irá examiná-lo ao vivo.", "Chaque analyste l'examinera en direct.", "Каждый аналитик разберёт это вживую."],
    "Capital is quarantined.": ["资金已被隔离。", "El capital está en cuarentena.", "पूंजी क्वारंटीन में है।", "رأس المال محجور.", "O capital está em quarentena.", "Le capital est en quarantaine.", "Капитал в карантине."],
    "No rule objected to this trade.": ["没有规则对该交易提出异议。", "Ninguna regla objetó esta operación.", "किसी नियम ने इस ट्रेड पर आपत्ति नहीं की।", "لم تعترض أي قاعدة على هذه الصفقة.", "Nenhuma regra se opôs a esta operação.", "Aucune règle n'a objecté à cette transaction.", "Ни одно правило не возразило против этой сделки."],
    "Nothing matched": ["无匹配项", "Nada coincidió", "कोई मेल नहीं", "لا تطابق", "Nada correspondeu", "Aucune correspondance", "Совпадений нет"],
    "Nothing yet": ["暂无", "Nada aún", "अभी कुछ नहीं", "لا شيء بعد", "Ainda nada", "Rien pour l'instant", "Пока ничего"],
    "Could not load": ["无法加载", "No se pudo cargar", "लोड नहीं हो सका", "تعذّر التحميل", "Não foi possível carregar", "Impossible de charger", "Не удалось загрузить"],
    "Could not reach Binance": ["无法连接币安", "No se pudo conectar con Binance", "बायनेंस तक नहीं पहुँच सके", "تعذّر الوصول إلى بينانس", "Não foi possível alcançar a Binance", "Impossible de joindre Binance", "Не удалось связаться с Binance"],
    "Couldn't analyze that pair": ["无法分析该交易对", "No se pudo analizar ese par", "उस जोड़ी का विश्लेषण नहीं हो सका", "تعذّر تحليل ذلك الزوج", "Não foi possível analisar esse par", "Impossible d'analyser cette paire", "Не удалось проанализировать эту пару"],
    "Engine not started": ["引擎未启动", "Motor no iniciado", "इंजन शुरू नहीं हुआ", "لم يبدأ المحرك", "Motor não iniciado", "Moteur non démarré", "Движок не запущен"],
    "Start the engine.": ["请启动引擎。", "Inicia el motor.", "इंजन शुरू करें।", "شغّل المحرك.", "Inicie o motor.", "Démarrez le moteur.", "Запустите движок."],
    "The first read appears within a tick.": ["第一次读数会在一个跳动内出现。", "La primera lectura aparece en un tick.", "पहली रीडिंग एक टिक के भीतर दिखती है।", "تظهر القراءة الأولى خلال نبضة واحدة.", "A primeira leitura aparece dentro de um tick.", "La première lecture apparaît en un tick.", "Первое считывание появляется в течение одного тика."],
    "Threat is critical.": ["威胁为危急。", "La amenaza es crítica.", "खतरा गंभीर है।", "التهديد حرج.", "A ameaça é crítica.", "La menace est critique.", "Угроза критическая."],
    "Threat is elevated.": ["威胁已升高。", "La amenaza es elevada.", "खतरा बढ़ा हुआ है।", "التهديد مرتفع.", "A ameaça está elevada.", "La menace est élevée.", "Угроза повышена."],
    "Analysis failed": ["分析失败", "El análisis falló", "विश्लेषण विफल", "فشل التحليل", "A análise falhou", "L'analyse a échoué", "Анализ не удался"],
    "Futures order cancelled.": ["合约订单已取消。", "Orden de futuros cancelada.", "फ्यूचर्स ऑर्डर रद्द।", "تم إلغاء أمر العقود الآجلة.", "Ordem de futuros cancelada.", "Ordre futures annulé.", "Фьючерсный ордер отменён."],
    "Market-wide crash injected. Watch the Guardian.": ["已注入全市场崩盘。请关注守护者。", "Colapso de todo el mercado inyectado. Observa al Guardián.", "पूरे बाज़ार का क्रैश इंजेक्ट किया गया। गार्डियन देखें।", "تم حقن انهيار على مستوى السوق. راقب الحارس.", "Colapso de todo o mercado injetado. Observe o Guardião.", "Krach de tout le marché injecté. Surveillez le Gardien.", "Введён обвал всего рынка. Следите за Стражем."],
    "Removed.": ["已移除。", "Eliminado.", "हटाया गया।", "تمت الإزالة.", "Removido.", "Supprimé.", "Удалено."],
    "Calibration applied — check the Track Record tab.": ["已应用校准——请查看历史记录标签页。", "Calibración aplicada: revisa la pestaña Historial.", "कैलिब्रेशन लागू — ट्रैक रिकॉर्ड टैब देखें।", "تم تطبيق المعايرة — راجع تبويب السجل.", "Calibração aplicada — verifique o separador Histórico.", "Calibrage appliqué — consultez l'onglet Historique.", "Калибровка применена — откройте вкладку истории."],
    "Model connected — the desk is now fully conversational.": ["模型已连接——问询台现已完全支持对话。", "Modelo conectado: la mesa ahora es totalmente conversacional.", "मॉडल कनेक्ट — डेस्क अब पूरी तरह संवादात्मक।", "تم توصيل النموذج — أصبح المكتب الآن حواريًا بالكامل.", "Modelo ligado — a mesa é agora totalmente conversacional.", "Modèle connecté — le bureau est maintenant entièrement conversationnel.", "Модель подключена — деск теперь полностью диалоговый."],
    "Could not reach the GlassBox backend. Is it running?": ["无法连接 GlassBox 后端。它在运行吗？", "No se pudo conectar con el backend de GlassBox. ¿Está en ejecución?", "GlassBox बैकएंड तक नहीं पहुँच सके। क्या यह चल रहा है?", "تعذّر الوصول إلى خادم GlassBox. هل هو قيد التشغيل؟", "Não foi possível alcançar o backend do GlassBox. Está em execução?", "Impossible de joindre le backend GlassBox. Est-il en cours d'exécution ?", "Не удалось связаться с бэкендом GlassBox. Он запущен?"],
    "Confirm you understand before applying.": ["应用前请确认你已理解。", "Confirma que lo entiendes antes de aplicar.", "लागू करने से पहले पुष्टि करें कि आप समझते हैं।", "أكِّد فهمك قبل التطبيق.", "Confirme que compreende antes de aplicar.", "Confirmez que vous comprenez avant d'appliquer.", "Подтвердите, что понимаете, перед применением."],
    "Tick the confirmation checkbox before going live.": ["切换到实盘前请勾选确认框。", "Marca la casilla de confirmación antes de pasar a real.", "लाइव जाने से पहले पुष्टि चेकबॉक्स टिक करें।", "حدّد مربع التأكيد قبل الانتقال إلى المباشر.", "Marque a caixa de confirmação antes de passar ao modo real.", "Cochez la case de confirmation avant de passer en réel.", "Отметьте флажок подтверждения перед переходом в реальный режим."],
    "Enter an address or transaction hash.": ["请输入地址或交易哈希。", "Introduce una dirección o hash de transacción.", "पता या लेन-देन हैश दर्ज करें।", "أدخل عنوانًا أو بصمة معاملة.", "Introduza um endereço ou hash de transação.", "Saisissez une adresse ou un hash de transaction.", "Введите адрес или хеш транзакции."],
    "Enter both a name and an address.": ["请同时输入名称和地址。", "Introduce un nombre y una dirección.", "नाम और पता दोनों दर्ज करें।", "أدخل الاسم والعنوان معًا.", "Introduza um nome e um endereço.", "Saisissez un nom et une adresse.", "Введите имя и адрес."],
    "Enter the URL you plan to host the document at first.": ["请先输入你计划托管该文档的 URL。", "Introduce primero la URL donde alojarás el documento.", "पहले वह URL दर्ज करें जहाँ आप दस्तावेज़ होस्ट करेंगे।", "أدخل أولًا عنوان URL الذي تنوي استضافة المستند عليه.", "Introduza primeiro o URL onde vai alojar o documento.", "Saisissez d'abord l'URL où vous comptez héberger le document.", "Сначала введите URL, где разместите документ."],
    "Copied. Host this exact file at the URL above, then click Save Client ID.": ["已复制。请在上方 URL 托管此文件，然后点击 Save Client ID。", "Copiado. Aloja este archivo exacto en la URL de arriba y luego haz clic en Save Client ID.", "कॉपी किया गया। ऊपर दिए URL पर यही फ़ाइल होस्ट करें, फिर Save Client ID क्लिक करें।", "تم النسخ. استضِف هذا الملف بالضبط على العنوان أعلاه، ثم انقر Save Client ID.", "Copiado. Aloje este ficheiro exato no URL acima e clique em Save Client ID.", "Copié. Hébergez ce fichier exact à l'URL ci-dessus, puis cliquez sur Save Client ID.", "Скопировано. Разместите именно этот файл по адресу выше, затем нажмите Save Client ID."],
    "not configured — Authorize with Binance is unavailable until this is set": ["未配置——在设置之前无法使用 Authorize with Binance", "no configurado: Autorizar con Binance no está disponible hasta que se establezca", "कॉन्फ़िगर नहीं — इसे सेट करने तक Authorize with Binance उपलब्ध नहीं", "غير مُهيّأ — خيار Authorize with Binance غير متاح حتى يتم ضبط هذا", "não configurado — Autorizar com a Binance fica indisponível até isto ser definido", "non configuré — Autoriser avec Binance est indisponible tant que ce n'est pas défini", "не настроено — «Авторизация через Binance» недоступна, пока это не задано"],
    "Closest so far:": ["目前最接近：", "Lo más cercano hasta ahora:", "अब तक सबसे नज़दीक:", "الأقرب حتى الآن:", "O mais próximo até agora:", "Le plus proche jusqu'ici :", "Ближайшее на данный момент:"],
    "Couldn't load:": ["无法加载：", "No se pudo cargar:", "लोड नहीं हो सका:", "تعذّر التحميل:", "Não foi possível carregar:", "Impossible de charger :", "Не удалось загрузить:"],
    "The system has changed its mind on a position you hold.": ["系统改变了对你所持某个持仓的判断。", "El sistema ha cambiado de opinión sobre una posición que mantienes.", "सिस्टम ने आपकी एक होल्डिंग पर अपना विचार बदला है।", "غيّر النظام رأيه بشأن مركز تحتفظ به.", "O sistema mudou de ideias sobre uma posição que detém.", "Le système a changé d'avis sur une position que vous détenez.", "Система изменила мнение по одной из ваших позиций."],

    // --- tooltips ---
    "How much of GlassBox's self-imposed Binance API budget is used this minute. Deliberately capped well below Binance's real limit. Green is comfortable; higher just means more data is being pulled.": ["本分钟内已使用的 GlassBox 自设币安 API 预算比例。刻意设定得远低于币安的真实限额。绿色表示宽裕；数值越高只是表示拉取的数据更多。", "Cuánto del presupuesto de API de Binance autoimpuesto por GlassBox se usa este minuto. Limitado deliberadamente muy por debajo del límite real de Binance. El verde es cómodo; más alto solo significa que se están extrayendo más datos.", "इस मिनट में GlassBox के स्व-निर्धारित बायनेंस API बजट का कितना उपयोग हुआ। जानबूझकर बायनेंस की असली सीमा से काफ़ी नीचे रखा गया। हरा सहज है; अधिक का मतलब केवल अधिक डेटा खींचा जा रहा है।", "مقدار ما استُخدم هذه الدقيقة من ميزانية GlassBox الذاتية لواجهة برمجة بينانس. محدود عمدًا أدنى بكثير من الحد الحقيقي لبينانس. الأخضر مريح؛ والأعلى يعني فقط سحب مزيد من البيانات.", "Quanto do orçamento de API da Binance autoimposto pela GlassBox é usado neste minuto. Limitado deliberadamente bem abaixo do limite real da Binance. Verde é confortável; mais alto significa apenas que estão a ser obtidos mais dados.", "La part du budget d'API Binance que GlassBox s'impose, utilisée cette minute. Plafonnée délibérément bien en dessous de la vraie limite de Binance. Le vert est confortable ; plus haut signifie simplement que davantage de données sont récupérées.", "Какая доля самоограниченного бюджета API Binance у GlassBox израсходована за эту минуту. Намеренно установлена намного ниже реального лимита Binance. Зелёный — комфортно; выше просто означает, что тянется больше данных."],
    "Live websocket price stream. 'live' means real Binance prices are flowing over one socket at zero API cost. The number is how many symbols are streaming right now.": ["实时 WebSocket 价格流。live 表示币安真实价格正通过单个套接字以零 API 成本推送。数字表示当前正在推送的品种数量。", "Flujo de precios por websocket en vivo. 'live' significa que precios reales de Binance fluyen por un socket con coste de API cero. El número es cuántos símbolos se transmiten ahora.", "लाइव वेबसॉकेट मूल्य स्ट्रीम। 'live' का अर्थ है असली बायनेंस कीमतें एक ही सॉकेट पर शून्य API लागत पर आ रही हैं। संख्या बताती है अभी कितने सिंबल स्ट्रीम हो रहे हैं।", "بث أسعار مباشر عبر websocket. تعني 'live' أن أسعار بينانس الحقيقية تتدفق عبر مقبس واحد بتكلفة API صفرية. الرقم يوضح عدد الرموز المتدفقة الآن.", "Fluxo de preços por websocket ao vivo. 'live' significa que preços reais da Binance fluem por um socket com custo de API zero. O número indica quantos símbolos estão a transmitir agora.", "Flux de prix par websocket en direct. 'live' signifie que de vrais prix Binance circulent sur un seul socket à coût d'API nul. Le nombre indique combien de symboles sont diffusés en ce moment.", "Живой поток цен по websocket. 'live' означает, что реальные цены Binance идут по одному сокету с нулевой стоимостью API. Число — сколько символов транслируется сейчас."],
    "Where market data comes from. 'Binance live' = real prices from Binance. 'simulated' = the seeded offline market used by stress tests.": ["行情数据的来源。Binance live ＝来自币安的真实价格。simulated ＝压力测试所用的预置离线行情。", "De dónde provienen los datos de mercado. 'Binance live' = precios reales de Binance. 'simulated' = el mercado offline predefinido usado en las pruebas de estrés.", "बाज़ार डेटा कहाँ से आता है। 'Binance live' = बायनेंस से असली कीमतें। 'simulated' = स्ट्रेस टेस्ट में उपयोग किया गया सीड किया ऑफ़लाइन मार्केट।", "مصدر بيانات السوق. 'Binance live' = أسعار حقيقية من بينانس. 'simulated' = السوق دون اتصال المُعد مسبقًا المستخدم في اختبارات الضغط.", "De onde vêm os dados de mercado. 'Binance live' = preços reais da Binance. 'simulated' = o mercado offline pré-definido usado nos testes de stress.", "D'où proviennent les données de marché. 'Binance live' = vrais prix de Binance. 'simulated' = le marché hors ligne prédéfini utilisé par les tests de résistance.", "Откуда берутся рыночные данные. 'Binance live' = реальные цены с Binance. 'simulated' = заданный офлайн-рынок для стресс-тестов."],
    "Model Context Protocol over Streamable HTTP": ["基于 Streamable HTTP 的 Model Context Protocol", "Model Context Protocol sobre Streamable HTTP", "Streamable HTTP पर Model Context Protocol", "بروتوكول Model Context عبر Streamable HTTP", "Model Context Protocol sobre Streamable HTTP", "Model Context Protocol via Streamable HTTP", "Model Context Protocol поверх Streamable HTTP"],
    "Binance can see the orders an agent places, but not why. This is the missing half.": ["币安能看到智能体下的订单，却看不到原因。这正是缺失的另一半。", "Binance puede ver las órdenes que coloca un agente, pero no por qué. Esta es la mitad que falta.", "बायनेंस देख सकता है कि एजेंट कौन-से ऑर्डर लगाता है, पर क्यों नहीं। यही छूटा हुआ आधा हिस्सा है।", "يستطيع بينانس رؤية الأوامر التي ينفّذها الوكيل، لكن ليس سببها. هذا هو النصف المفقود.", "A Binance vê as ordens que um agente coloca, mas não o porquê. Esta é a metade que falta.", "Binance voit les ordres qu'un agent passe, mais pas pourquoi. C'est la moitié manquante.", "Binance видит ордера, которые ставит агент, но не причины. Это недостающая половина."],
    "Deterministic code, evaluated after the AI finishes reasoning and before anything reaches an exchange.": ["确定性代码，在 AI 完成推理之后、任何内容到达交易所之前执行。", "Código determinista, evaluado después de que la IA termina de razonar y antes de que algo llegue a un exchange.", "नियतात्मक कोड, AI के तर्क समाप्त करने के बाद और कुछ भी एक्सचेंज तक पहुँचने से पहले मूल्यांकित।", "كود حتمي، يُقيَّم بعد انتهاء الذكاء الاصطناعي من الاستدلال وقبل وصول أي شيء إلى منصة تداول.", "Código determinístico, avaliado depois de a IA terminar o raciocínio e antes de algo chegar a uma exchange.", "Code déterministe, évalué après que l'IA a fini de raisonner et avant que quoi que ce soit n'atteigne une plateforme.", "Детерминированный код, выполняется после того, как ИИ завершил рассуждение, и до того, как что-либо попадёт на биржу."],
    "Five analysts examine each asset independently. Agreement across independent method families counts as real confluence; a single-family view is discounted, and disagreement is priced into size.": ["五位分析师各自独立分析每项资产。跨独立方法族的一致才算真正的共振；单一方法族的观点会被打折，分歧则计入头寸规模。", "Cinco analistas examinan cada activo de forma independiente. La coincidencia entre familias de métodos independientes cuenta como confluencia real; una visión de una sola familia se descuenta y el desacuerdo se refleja en el tamaño.", "पाँच विश्लेषक प्रत्येक एसेट का स्वतंत्र रूप से विश्लेषण करते हैं। स्वतंत्र मेथड-परिवारों में सहमति ही असली संगम मानी जाती है; एकल-परिवार दृष्टिकोण को कम आँका जाता है और असहमति साइज़ में शामिल होती है।", "يفحص خمسة محللين كل أصل بشكل مستقل. الاتفاق عبر عائلات أساليب مستقلة يُعدّ تقاربًا حقيقيًا؛ ورأي عائلة واحدة يُخصم، ويُدرَج الاختلاف في حجم المركز.", "Cinco analistas examinam cada ativo de forma independente. A concordância entre famílias de métodos independentes conta como confluência real; uma visão de uma só família é descontada, e a discordância entra no tamanho.", "Cinq analystes examinent chaque actif indépendamment. L'accord entre familles de méthodes indépendantes compte comme une vraie confluence ; une vue d'une seule famille est décotée, et le désaccord est intégré à la taille.", "Пять аналитиков независимо изучают каждый актив. Согласие между независимыми семействами методов — это настоящее схождение; взгляд одного семейства обесценивается, а разногласие закладывается в размер позиции."],
    "A real wallet on Base Sepolia (testnet) used to settle agent-to-agent payments. Holds no real value.": ["Base Sepolia（测试网）上的真实钱包，用于结算智能体之间的支付。不持有任何真实价值。", "Una billetera real en Base Sepolia (testnet) usada para liquidar pagos entre agentes. No tiene valor real.", "Base Sepolia (टेस्टनेट) पर एक असली वॉलेट, एजेंट-से-एजेंट भुगतान निपटाने के लिए। इसमें कोई असली मूल्य नहीं।", "محفظة حقيقية على Base Sepolia (شبكة اختبار) تُستخدم لتسوية المدفوعات بين الوكلاء. لا تحمل أي قيمة حقيقية.", "Uma carteira real na Base Sepolia (testnet) usada para liquidar pagamentos entre agentes. Não tem valor real.", "Un vrai portefeuille sur Base Sepolia (testnet) servant à régler les paiements entre agents. Ne détient aucune valeur réelle.", "Реальный кошелёк в Base Sepolia (тестовая сеть) для расчётов между агентами. Не хранит реальной ценности."],
    "Paste a wallet address or transaction hash from Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism or Avalanche. Read directly from each chain — nothing is signed or sent.": ["粘贴来自 Bitcoin、Ethereum、Solana、BNB Smart Chain、Base、Arbitrum、Polygon、Optimism 或 Avalanche 的钱包地址或交易哈希。直接从各条链读取——不会签名或发送任何内容。", "Pega una dirección de billetera o un hash de transacción de Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism o Avalanche. Se lee directamente de cada cadena: nada se firma ni se envía.", "Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism या Avalanche से वॉलेट पता या लेन-देन हैश पेस्ट करें। हर चेन से सीधे पढ़ा जाता है — कुछ भी हस्ताक्षरित या भेजा नहीं जाता।", "الصق عنوان محفظة أو بصمة معاملة من Bitcoin أو Ethereum أو Solana أو BNB Smart Chain أو Base أو Arbitrum أو Polygon أو Optimism أو Avalanche. تتم القراءة مباشرة من كل سلسلة — لا شيء يُوقَّع أو يُرسَل.", "Cole um endereço de carteira ou hash de transação de Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism ou Avalanche. Lido diretamente de cada cadeia — nada é assinado ou enviado.", "Collez une adresse de portefeuille ou un hash de transaction de Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism ou Avalanche. Lu directement depuis chaque chaîne — rien n'est signé ni envoyé.", "Вставьте адрес кошелька или хеш транзакции из Bitcoin, Ethereum, Solana, BNB Smart Chain, Base, Arbitrum, Polygon, Optimism или Avalanche. Читается напрямую из каждой сети — ничего не подписывается и не отправляется."],
    "OAuth 2.1 + PKCE, opened in a new tab. No shell required.": ["OAuth 2.1 + PKCE，在新标签页中打开。无需命令行。", "OAuth 2.1 + PKCE, abierto en una pestaña nueva. No requiere terminal.", "OAuth 2.1 + PKCE, नए टैब में खुलता है। कोई शेल आवश्यक नहीं।", "OAuth 2.1 + PKCE، يُفتح في تبويب جديد. لا حاجة إلى طرفية.", "OAuth 2.1 + PKCE, aberto num novo separador. Não requer terminal.", "OAuth 2.1 + PKCE, ouvert dans un nouvel onglet. Aucun terminal requis.", "OAuth 2.1 + PKCE, открывается в новой вкладке. Терминал не нужен."],
    "Fed H.15 yields and the scheduled FOMC calendar — de-risks before the event, not after": ["美联储 H.15 收益率与既定的 FOMC 日程——在事件之前而非之后降低风险", "Rendimientos H.15 de la Fed y el calendario FOMC programado: reduce el riesgo antes del evento, no después", "फेड H.15 यील्ड और निर्धारित FOMC कैलेंडर — घटना से पहले जोखिम घटाता है, बाद में नहीं", "عوائد Fed H.15 وجدول FOMC المقرّر — يقلّل المخاطرة قبل الحدث لا بعده", "Rendimentos H.15 da Fed e o calendário FOMC agendado — reduz o risco antes do evento, não depois", "Rendements H.15 de la Fed et le calendrier FOMC programmé — réduit le risque avant l'événement, pas après", "Доходности ФРС H.15 и запланированный календарь FOMC — снижает риск до события, а не после"],
    "GlassBox is spot-only — every held position was bought, never sold short": ["GlassBox 仅做现货——每个持仓都是买入的，从不做空", "GlassBox es solo spot: cada posición mantenida fue comprada, nunca vendida en corto", "GlassBox केवल स्पॉट है — हर होल्ड की गई पोज़िशन खरीदी गई थी, कभी शॉर्ट नहीं बेची", "GlassBox للفوري فقط — كل مركز محتفظ به تم شراؤه، ولم يُبع على المكشوف قط", "A GlassBox é apenas spot — cada posição detida foi comprada, nunca vendida a descoberto", "GlassBox est uniquement au comptant — chaque position détenue a été achetée, jamais vendue à découvert", "GlassBox только спот — каждая удерживаемая позиция куплена, никогда не продавалась в шорт"],
    "A theme is only tradable while attention is still building and price has not caught up.": ["只有当关注度仍在上升、价格尚未跟上时，主题才可交易。", "Un tema solo es negociable mientras la atención sigue creciendo y el precio no ha alcanzado.", "कोई थीम केवल तभी ट्रेड-योग्य है जब ध्यान अभी बढ़ रहा हो और कीमत ने पकड़ न बनाई हो।", "لا يمكن تداول السردية إلا بينما الاهتمام لا يزال يتزايد والسعر لم يلحق بعد.", "Um tema só é negociável enquanto a atenção ainda está a crescer e o preço não acompanhou.", "Un thème n'est négociable que tant que l'attention monte encore et que le prix n'a pas rattrapé.", "Тема торгуема, только пока внимание растёт, а цена ещё не догнала."],
    "Seeded simulated market. Set GLASSBOX_LIVE_DATA=1 for real Binance data.": ["预置模拟行情。设置 GLASSBOX_LIVE_DATA=1 以获取币安真实数据。", "Mercado simulado predefinido. Establece GLASSBOX_LIVE_DATA=1 para datos reales de Binance.", "सीड किया गया सिम्युलेटेड मार्केट। असली बायनेंस डेटा के लिए GLASSBOX_LIVE_DATA=1 सेट करें।", "سوق محاكى مُعد مسبقًا. اضبط GLASSBOX_LIVE_DATA=1 للحصول على بيانات بينانس الحقيقية.", "Mercado simulado pré-definido. Defina GLASSBOX_LIVE_DATA=1 para dados reais da Binance.", "Marché simulé prédéfini. Définissez GLASSBOX_LIVE_DATA=1 pour de vraies données Binance.", "Заданный симулированный рынок. Установите GLASSBOX_LIVE_DATA=1 для реальных данных Binance."],
    "Run the full five-analyst Council on a coin the engine doesn't trade — a complete verdict on demand. To discuss a pair in plain English, use the chat button in the bottom-right corner.": ["对引擎不交易的币种运行完整的五分析师议会——按需给出完整裁决。若要用自然语言讨论某个交易对，请使用右下角的聊天按钮。", "Ejecuta el Consejo completo de cinco analistas sobre una moneda que el motor no opera: un veredicto completo a demanda. Para hablar de un par en lenguaje natural, usa el botón de chat en la esquina inferior derecha.", "इंजन जिस सिक्के का ट्रेड नहीं करता उस पर पूरी पाँच-विश्लेषक परिषद चलाएँ — मांग पर पूर्ण निर्णय। किसी जोड़ी पर सामान्य भाषा में चर्चा के लिए नीचे-दाएँ कोने का चैट बटन उपयोग करें।", "شغّل مجلس المحللين الخمسة الكامل على عملة لا يتداولها المحرك — حكم كامل عند الطلب. لمناقشة زوج بلغة عادية، استخدم زر الدردشة في الزاوية السفلية اليمنى.", "Execute o Conselho completo de cinco analistas sobre uma moeda que o motor não negocia — um veredito completo a pedido. Para discutir um par em linguagem simples, use o botão de chat no canto inferior direito.", "Lancez le Conseil complet de cinq analystes sur une crypto que le moteur ne trade pas — un verdict complet à la demande. Pour discuter d'une paire en langage courant, utilisez le bouton de chat en bas à droite.", "Запустите полный Совет из пяти аналитиков по монете, которой движок не торгует, — полный вердикт по запросу. Чтобы обсудить пару обычным языком, используйте кнопку чата в правом нижнем углу."],
    "Ask me about any Binance pair — including ones the engine doesn't trade. For example:": ["向我询问任何币安交易对——包括引擎不交易的。例如：", "Pregúntame sobre cualquier par de Binance, incluidos los que el motor no opera. Por ejemplo:", "मुझसे किसी भी बायनेंस जोड़ी के बारे में पूछें — उनमें भी जो इंजन ट्रेड नहीं करता। उदाहरण के लिए:", "اسألني عن أي زوج على بينانس — بما في ذلك ما لا يتداوله المحرك. على سبيل المثال:", "Pergunte-me sobre qualquer par da Binance — incluindo os que o motor não negocia. Por exemplo:", "Posez-moi une question sur n'importe quelle paire Binance — y compris celles que le moteur ne trade pas. Par exemple :", "Спросите меня о любой паре Binance — включая те, которыми движок не торгует. Например:"],
    "— no net direction at all, but sharp swings in both directions: tests whether the system avoids getting whipsawed into repeated small losses.": ["—完全没有净方向，但双向剧烈摆动：检验系统是否避免被反复的小额亏损来回打脸。", "— sin ninguna dirección neta, pero con oscilaciones bruscas en ambos sentidos: prueba si el sistema evita quedar atrapado en pérdidas pequeñas repetidas.", "— कोई शुद्ध दिशा नहीं, पर दोनों ओर तीखे झूले: परखता है कि सिस्टम बार-बार छोटे नुकसानों में फँसने से बचता है या नहीं।", "— لا اتجاه صافٍ على الإطلاق، لكن تأرجحات حادة في الاتجاهين: يختبر ما إذا كان النظام يتجنّب الوقوع في خسائر صغيرة متكررة.", "— sem qualquer direção líquida, mas com oscilações bruscas em ambos os sentidos: testa se o sistema evita ser apanhado em perdas pequenas repetidas.", "— aucune direction nette, mais de fortes oscillations dans les deux sens : teste si le système évite de se faire ballotter en petites pertes répétées.", "— вообще без чистого направления, но с резкими колебаниями в обе стороны: проверяет, избегает ли система череды мелких убытков от пилы."],
    "— the change takes effect immediately, and the ledger records exactly what changed and when, so a rule change is as auditable as a trade.": ["—变更立即生效，账本会精确记录改了什么以及何时改的，因此一次规则变更与一笔交易同样可审计。", "— el cambio surte efecto de inmediato, y el libro registra exactamente qué cambió y cuándo, así que un cambio de regla es tan auditable como una operación.", "— परिवर्तन तुरंत प्रभावी होता है, और बहीखाता ठीक-ठीक दर्ज करता है कि क्या और कब बदला, इसलिए नियम परिवर्तन एक ट्रेड जितना ही ऑडिट-योग्य है।", "— يسري التغيير فورًا، ويسجّل الدفتر بدقة ما تغيّر ومتى، فيكون تغيير القاعدة قابلًا للتدقيق مثل الصفقة تمامًا.", "— a alteração produz efeito imediato, e o livro-razão regista exatamente o que mudou e quando, por isso uma alteração de regra é tão auditável como uma operação.", "— le changement prend effet immédiatement, et le registre consigne exactement ce qui a changé et quand, de sorte qu'un changement de règle est aussi auditable qu'une transaction.", "— изменение вступает в силу немедленно, и реестр точно фиксирует, что и когда изменилось, поэтому изменение правила так же проверяемо, как сделка."],
    "Try a different search or quote asset.": ["请尝试其他搜索词或计价资产。", "Prueba con otra búsqueda o activo de cotización.", "कोई दूसरी खोज या कोट एसेट आज़माएँ।", "جرّب بحثًا آخر أو أصل تسعير مختلفًا.", "Tente outra pesquisa ou ativo de cotação.", "Essayez une autre recherche ou un autre actif de cotation.", "Попробуйте другой поиск или котируемый актив."],
    "· Optional. The desk already works without a model — a key just makes it fully conversational. Your key stays on this machine and is only sent to the provider you choose.": ["· 可选。即使不接入模型，问询台也能工作——密钥只是让它完全支持对话。你的密钥留在本机，且只发送给你选择的提供商。", "· Opcional. La mesa ya funciona sin un modelo: una clave solo la hace totalmente conversacional. Tu clave permanece en esta máquina y solo se envía al proveedor que elijas.", "· वैकल्पिक। डेस्क बिना मॉडल के भी काम करता है — कुंजी केवल इसे पूरी तरह संवादात्मक बनाती है। आपकी कुंजी इसी मशीन पर रहती है और केवल आपके चुने प्रदाता को भेजी जाती है।", "· اختياري. يعمل المكتب بالفعل دون نموذج — المفتاح يجعله حواريًا بالكامل فقط. يبقى مفتاحك على هذا الجهاز ولا يُرسَل إلا إلى المزوّد الذي تختاره.", "· Opcional. A mesa já funciona sem um modelo — uma chave apenas a torna totalmente conversacional. A sua chave permanece nesta máquina e só é enviada ao fornecedor que escolher.", "· Facultatif. Le bureau fonctionne déjà sans modèle — une clé le rend simplement entièrement conversationnel. Votre clé reste sur cette machine et n'est envoyée qu'au fournisseur que vous choisissez.", "· Необязательно. Деск уже работает без модели — ключ лишь делает его полностью диалоговым. Ваш ключ остаётся на этой машине и отправляется только выбранному вами провайдеру."],

    // --- verdict badges (both cases) ---
    "bullish": ["看涨", "alcista", "तेज़ी", "صاعد", "alta", "haussier", "бычий"],
    "bearish": ["看跌", "bajista", "मंदी", "هابط", "baixa", "baissier", "медвежий"],
    "Bullish": ["看涨", "Alcista", "तेज़ी", "صاعد", "Alta", "Haussier", "Бычий"],
    "Bearish": ["看跌", "Bajista", "मंदी", "هابط", "Baixa", "Baissier", "Медвежий"],
    "Neutral": ["中性", "Neutral", "तटस्थ", "محايد", "Neutro", "Neutre", "Нейтрально"],
    "current": ["当前", "actual", "वर्तमान", "الحالي", "atual", "actuel", "текущий"],
    "binance public": ["币安公开", "Binance público", "बायनेंस पब्लिक", "بينانس العام", "Binance público", "Binance public", "Binance публичный"],

    // --- mode names / descriptions ---
    "Bridge": ["桥接", "Puente", "ब्रिज", "جسر", "Ponte", "Pont", "Мост"],
    "A real MCP client against a local stand-in for Binance. No money at risk.": ["针对本地币安替身运行的真实 MCP 客户端。不涉及任何真实资金。", "Un cliente MCP real contra un sustituto local de Binance. Sin dinero en riesgo.", "स्थानीय बायनेंस स्टैंड-इन के विरुद्ध एक असली MCP क्लाइंट। कोई पैसा जोखिम में नहीं।", "عميل MCP حقيقي مقابل بديل محلي لبينانس. لا مال في خطر.", "Um cliente MCP real contra um substituto local da Binance. Sem dinheiro em risco.", "Un vrai client MCP face à un substitut local de Binance. Aucun argent en jeu.", "Настоящий MCP-клиент против локальной замены Binance. Деньги не под угрозой."],

    // --- stress-scenario dropdown options ---
    "Calm drift": ["平静漂移", "Deriva tranquila", "शांत बहाव", "انجراف هادئ", "Deriva calma", "Dérive calme", "Спокойный дрейф"],
    "Trending bull": ["趋势上行", "Toro en tendencia", "ट्रेंडिंग बुल", "صعود متجه", "Touro em tendência", "Marché haussier", "Трендовый бык"],
    "Grinding bear": ["缓慢下行", "Oso lento", "धीमा बियर", "هبوط بطيء", "Urso lento", "Ours poussif", "Вялый медведь"],
    "Violent chop": ["剧烈震荡", "Vaivén violento", "तीव्र चॉप", "تذبذب عنيف", "Oscilação violenta", "Balancement violent", "Резкая пила"],
    "Cascading crash": ["级联崩盘", "Colapso en cascada", "कैस्केडिंग क्रैश", "انهيار متتالٍ", "Colapso em cascata", "Krach en cascade", "Каскадный обвал"],

    // --- final Payments / Yield / Ledger / Sentinel labels ---
    "(unfunded)": ["（未注资）", "(sin fondos)", "(अनफंडेड)", "(غير مموّل)", "(sem fundos)", "(non approvisionné)", "(без средств)"],
    "active": ["活跃", "activo", "सक्रिय", "نشط", "ativo", "actif", "активно"],
    "Address": ["地址", "Dirección", "पता", "العنوان", "Endereço", "Adresse", "Адрес"],
    "Chain": ["链", "Cadena", "चेन", "السلسلة", "Cadeia", "Chaîne", "Сеть"],
    "Baseline": ["基线", "Referencia", "बेसलाइन", "خط الأساس", "Linha de base", "Référence", "Базовый уровень"],
    "checkpoint": ["检查点", "checkpoint", "चेकपॉइंट", "نقطة تفتيش", "checkpoint", "point de contrôle", "контрольная точка"],
    "Checkpoints": ["检查点", "Checkpoints", "चेकपॉइंट", "نقاط التفتيش", "Checkpoints", "Points de contrôle", "Контрольные точки"],
    "chosen": ["已选择", "elegido", "चुना गया", "مختار", "escolhido", "choisi", "выбрано"],
    "Base Sepolia (testnet)": ["Base Sepolia（测试网）", "Base Sepolia (testnet)", "Base Sepolia (टेस्टनेट)", "Base Sepolia (شبكة اختبار)", "Base Sepolia (testnet)", "Base Sepolia (testnet)", "Base Sepolia (тестовая сеть)"],
    "Add a counterparty, then payments will settle on-chain and appear here.": ["添加交易对手后，支付将在链上结算并显示在此处。", "Añade una contraparte y los pagos se liquidarán on-chain y aparecerán aquí.", "एक प्रतिपक्ष जोड़ें, फिर भुगतान ऑन-चेन निपटेंगे और यहाँ दिखेंगे।", "أضف طرفًا مقابلًا، وستُسوّى المدفوعات على السلسلة وتظهر هنا.", "Adicione uma contraparte e os pagamentos serão liquidados on-chain e aparecerão aqui.", "Ajoutez une contrepartie, puis les paiements seront réglés on-chain et apparaîtront ici.", "Добавьте контрагента, и платежи будут проводиться on-chain и появляться здесь."],
    "Add one above before any payment can be made.": ["在进行任何支付之前，请先在上方添加一个。", "Añade uno arriba antes de poder hacer cualquier pago.", "कोई भी भुगतान करने से पहले ऊपर एक जोड़ें।", "أضف واحدًا أعلاه قبل أن يتم أي دفع.", "Adicione um acima antes de poder efetuar qualquer pagamento.", "Ajoutez-en une ci-dessus avant de pouvoir effectuer un paiement.", "Добавьте выше одного, прежде чем можно будет провести платёж."],
    "This wallet holds zero real value and settles nothing until funded with free testnet ETH from a faucet — a one-time step using the operator's own faucet claim, not GlassBox's funds.": ["该钱包不持有任何真实价值，在通过水龙头注入免费测试网 ETH 之前不会结算任何内容——这是一次性步骤，使用操作者自己的水龙头领取额度，而非 GlassBox 的资金。", "Esta billetera no tiene ningún valor real y no liquida nada hasta financiarse con ETH gratuito de testnet desde un faucet: un paso único que usa el reclamo de faucet del propio operador, no fondos de GlassBox.", "यह वॉलेट शून्य असली मूल्य रखता है और तब तक कुछ नहीं निपटाता जब तक किसी फ़ॉसेट से मुफ़्त टेस्टनेट ETH से फंड न हो — यह एक बार का चरण है जो ऑपरेटर के अपने फ़ॉसेट क्लेम का उपयोग करता है, GlassBox के फंड का नहीं।", "لا تحمل هذه المحفظة أي قيمة حقيقية ولا تُسوّي شيئًا حتى تُموَّل بـ ETH مجاني من شبكة اختبار عبر صنبور — خطوة لمرة واحدة تستخدم مطالبة الصنبور الخاصة بالمشغّل، لا أموال GlassBox.", "Esta carteira não tem qualquer valor real e não liquida nada até ser financiada com ETH gratuito de testnet a partir de uma torneira — um passo único que usa o pedido de torneira do próprio operador, não fundos da GlassBox.", "Ce portefeuille ne détient aucune valeur réelle et ne règle rien tant qu'il n'est pas approvisionné en ETH de testnet gratuit depuis un faucet — une étape unique utilisant la réclamation de faucet de l'opérateur, pas les fonds de GlassBox.", "Этот кошелёк не имеет реальной ценности и ничего не проводит, пока не пополнен бесплатным тестовым ETH из крана — разовый шаг с использованием собственного запроса оператора к крану, а не средств GlassBox."],
    "A reading of 0/normal means the real market genuinely hasn't moved fast enough to register as a threat right now — this is computed fresh every tick, not a fixed value. To see it react, use": ["读数为 0／正常表示真实市场目前确实还没有快到足以被记为威胁——这是每个跳动实时计算的，而非固定值。若要看到它变化，请使用", "Una lectura de 0/normal significa que el mercado real aún no se ha movido lo bastante rápido como para registrarse como amenaza ahora mismo: se calcula de nuevo en cada tick, no es un valor fijo. Para verlo reaccionar, usa", "0/सामान्य की रीडिंग का अर्थ है कि असली बाज़ार अभी वास्तव में इतनी तेज़ी से नहीं चला कि खतरे के रूप में दर्ज हो — यह हर टिक पर ताज़ा गणना होती है, कोई स्थिर मान नहीं। इसे प्रतिक्रिया करते देखने के लिए, उपयोग करें", "تعني القراءة 0/طبيعي أن السوق الحقيقي لم يتحرك فعلًا بسرعة كافية ليُسجَّل كتهديد الآن — تُحسب من جديد كل نبضة، وليست قيمة ثابتة. لرؤيتها تتفاعل، استخدم", "Uma leitura de 0/normal significa que o mercado real ainda não se moveu depressa o suficiente para registar como ameaça agora — é calculado de novo a cada tick, não é um valor fixo. Para o ver reagir, use", "Une lecture de 0/normal signifie que le marché réel n'a pas encore bougé assez vite pour être enregistré comme une menace pour l'instant — c'est recalculé à chaque tick, ce n'est pas une valeur fixe. Pour le voir réagir, utilisez", "Значение 0/нормально означает, что реальный рынок сейчас действительно двигался недостаточно быстро, чтобы зарегистрироваться как угроза — это пересчитывается каждый тик, а не фиксированное значение. Чтобы увидеть реакцию, используйте"],
    "Standing": ["状态", "Situación", "स्थिति", "الحالة", "Situação", "Statut", "Положение"],
    "Graded": ["已评分", "Calificadas", "आँके गए", "مُقيَّم", "Avaliadas", "Notées", "Оценено"],
    "outperforming": ["表现优异", "rindiendo por encima", "बेहतर प्रदर्शन", "أداء متفوق", "a superar", "surperformance", "выше нормы"],
    "in line with baseline": ["与基准持平", "en línea con la referencia", "बेसलाइन के अनुरूप", "متوافق مع خط الأساس", "em linha com a base", "conforme à la référence", "на уровне базы"],
    "underperforming, vote down-weighted": ["表现不佳，投票权重下调", "rindiendo por debajo, voto con menor peso", "कमज़ोर प्रदर्शन, वोट का भार घटाया", "أداء ضعيف، خُفِّض وزن التصويت", "abaixo do esperado, voto com menos peso", "sous-performance, vote sous-pondéré", "ниже нормы, вес голоса снижен"],
    "no track record yet": ["暂无历史记录", "Aún sin historial", "अभी कोई ट्रैक रिकॉर्ड नहीं", "لا سجل أداء بعد", "Ainda sem histórico", "Aucun historique pour l'instant", "Пока нет истории"]
  };

  // Placeholders / titles that are worth translating (kept short; examples keep their tickers).
  var TR_ATTR = {
    "Ask about a coin or pair…": TR["Ask about a coin or pair…"],
    "Search symbol or asset": ["搜索代码或资产", "Buscar símbolo o activo", "प्रतीक या एसेट खोजें", "ابحث عن رمز أو أصل", "Pesquisar símbolo ou ativo", "Rechercher un symbole ou un actif", "Поиск символа или актива"],
    "Search asset, e.g. SOL": ["搜索资产，例如 SOL", "Buscar activo, p. ej. SOL", "एसेट खोजें, जैसे SOL", "ابحث عن أصل، مثل SOL", "Pesquisar ativo, ex. SOL", "Rechercher un actif, ex. SOL", "Поиск актива, напр. SOL"],
    "Click to browse, or type to search…": ["点击浏览，或输入以搜索…", "Haz clic para explorar o escribe para buscar…", "ब्राउज़ करने के लिए क्लिक करें, या खोजने के लिए टाइप करें…", "انقر للتصفح أو اكتب للبحث…", "Clique para navegar ou digite para pesquisar…", "Cliquez pour parcourir ou tapez pour rechercher…", "Нажмите для обзора или введите для поиска…"],
    "agent id, label, or address": ["智能体 ID、标签或地址", "id de agente, etiqueta o dirección", "एजेंट id, लेबल, या पता", "معرّف الوكيل أو تسمية أو عنوان", "id do agente, rótulo ou endereço", "id d'agent, libellé ou adresse", "id агента, метка или адрес"],
    "memo (optional)": ["备注（可选）", "nota (opcional)", "मेमो (वैकल्पिक)", "ملاحظة (اختياري)", "nota (opcional)", "mémo (facultatif)", "заметка (необязательно)"],
    "Counterparty name": ["交易对手名称", "Nombre de la contraparte", "प्रतिपक्ष का नाम", "اسم الطرف المقابل", "Nome da contraparte", "Nom de la contrepartie", "Имя контрагента"],
    "wallet address or transaction hash": ["钱包地址或交易哈希", "dirección de cartera o hash de transacción", "वॉलेट पता या लेन-देन हैश", "عنوان محفظة أو بصمة معاملة", "endereço de carteira ou hash de transação", "adresse de portefeuille ou hash de transaction", "адрес кошелька или хеш транзакции"]
  };

  var _origText = new WeakMap();
  var _origAttr = new WeakMap();
  var _observer = null;
  var ATTRS = ["placeholder", "title", "aria-label"];

  function _tr(str, lang) {
    var row = TR[str];
    return row ? row[LANGS.indexOf(lang)] : null;
  }
  function _trAttr(str, lang) {
    var row = TR_ATTR[str] || TR[str];
    return row ? row[LANGS.indexOf(lang)] : null;
  }

  // Pattern translation for analyst/telemetry sentences that carry live numbers.
  // Captured numeric groups ($1, $2) are re-inserted verbatim into the translation.
  var PATTERNS = [
    { re: /^Exchange flows balanced at ([+\-][\d,]+) units; no directional edge\.$/,
      t: ["交易所资金流均衡，净额 $1 单位；无方向性优势。",
          "Flujos de intercambio equilibrados en $1 unidades; sin ventaja direccional.",
          "एक्सचेंज फ्लो $1 यूनिट पर संतुलित; कोई दिशात्मक बढ़त नहीं।",
          "تدفقات المنصة متوازنة عند $1 وحدة؛ لا أفضلية اتجاهية.",
          "Fluxos da exchange equilibrados em $1 unidades; sem vantagem direcional.",
          "Flux d'échange équilibrés à $1 unités ; aucun avantage directionnel.",
          "Биржевые потоки сбалансированы на уровне $1 единиц; направленного преимущества нет."] },
    { re: /^Funding neutral at (-?[\d.]+) bps; positioning is not stretched\.$/,
      t: ["资金费中性，为 $1 个基点；持仓未过度倾斜。",
          "Financiación neutral en $1 pb; el posicionamiento no está tensionado.",
          "फंडिंग तटस्थ, $1 bps पर; पोज़िशनिंग अत्यधिक नहीं।",
          "التمويل محايد عند $1 نقطة أساس؛ التموضع غير مُبالَغ فيه.",
          "Financiamento neutro em $1 pb; o posicionamento não está esticado.",
          "Financement neutre à $1 pb ; le positionnement n'est pas tendu.",
          "Фандинг нейтральный на уровне $1 б.п.; позиционирование не перегрето."] },
    { re: /^Funding is negative at (-?[\d.]+) bps — shorts are paying, which is where squeezes start\.$/,
      t: ["资金费为负，为 $1 个基点——空头在付费，这正是逼空的起点。",
          "La financiación es negativa en $1 pb: los cortos pagan, que es donde empiezan los squeezes.",
          "फंडिंग नकारात्मक, $1 bps पर — शॉर्ट भुगतान कर रहे हैं, यहीं से स्क्वीज़ शुरू होते हैं।",
          "التمويل سالب عند $1 نقطة أساس — البائعون على المكشوف يدفعون، ومن هنا تبدأ عمليات الضغط.",
          "O financiamento é negativo em $1 pb — os vendidos estão a pagar, que é onde começam os squeezes.",
          "Le financement est négatif à $1 pb — les vendeurs paient, et c'est là que commencent les squeezes.",
          "Фандинг отрицательный на уровне $1 б.п. — шорты платят, именно отсюда начинаются сквизы."] },
    { re: /^Funding is ([+\-][\d.]+)% — longs are paying to hold, so positioning is crowded long and vulnerable to a squeeze\.$/,
      t: ["资金费为 $1%——多头在付费持仓，因此持仓偏拥挤做多，易受逼空冲击。",
          "La financiación es $1%: los largos pagan por mantener, así que el posicionamiento está saturado en largo y vulnerable a un squeeze.",
          "फंडिंग $1% — लॉन्ग होल्ड करने के लिए भुगतान कर रहे हैं, इसलिए पोज़िशनिंग भीड़भाड़ लॉन्ग और स्क्वीज़ के प्रति संवेदनशील।",
          "التمويل $1% — المشترون يدفعون للاحتفاظ، لذا التموضع مزدحم شراءً وعرضة للضغط.",
          "O financiamento é $1% — os comprados estão a pagar para manter, por isso o posicionamento está sobrelotado em compra e vulnerável a um squeeze.",
          "Le financement est $1% — les acheteurs paient pour tenir, le positionnement est donc saturé à l'achat et vulnérable à un squeeze.",
          "Фандинг $1% — лонги платят за удержание, поэтому позиционирование перегружено в лонг и уязвимо к сквизу."] },
    { re: /^Funding is ([+\-][\d.]+)% — shorts are paying, so the crowd is short and a rally would hurt them\.$/,
      t: ["资金费为 $1%——空头在付费，因此市场偏空，一旦上涨将令其受损。",
          "La financiación es $1%: los cortos pagan, así que la multitud está en corto y un repunte les perjudicaría.",
          "फंडिंग $1% — शॉर्ट भुगतान कर रहे हैं, इसलिए भीड़ शॉर्ट है और तेज़ी उन्हें नुकसान पहुँचाएगी।",
          "التمويل $1% — البائعون على المكشوف يدفعون، لذا الجمهور على المكشوف وأي صعود سيضرّهم.",
          "O financiamento é $1% — os vendidos estão a pagar, por isso a multidão está vendida e uma subida prejudicá-los-ia.",
          "Le financement est $1% — les vendeurs paient, la foule est donc vendeuse et une hausse leur nuirait.",
          "Фандинг $1% — шорты платят, поэтому толпа в шорте, и рост им навредит."] },
    { re: /^Funding is ([+\-][\d.]+)%, roughly neutral positioning\.$/,
      t: ["资金费为 $1%，持仓大致中性。",
          "La financiación es $1%, posicionamiento aproximadamente neutral.",
          "फंडिंग $1%, लगभग तटस्थ पोज़िशनिंग।",
          "التمويل $1%، تموضع محايد تقريبًا.",
          "O financiamento é $1%, posicionamento aproximadamente neutro.",
          "Le financement est $1%, positionnement à peu près neutre.",
          "Фандинг $1%, позиционирование примерно нейтральное."] },
    { re: /^Social tone ([+\-][\d.]+) across ([\d,]+) mentions in the last hour\.$/,
      t: ["过去一小时，社交情绪为 $1，共 $2 次提及。",
          "Tono social de $1 en $2 menciones en la última hora.",
          "पिछले घंटे में सोशल टोन $1, $2 उल्लेखों में।",
          "النبرة الاجتماعية $1 عبر $2 إشارة في الساعة الأخيرة.",
          "Tom social de $1 em $2 menções na última hora.",
          "Tonalité sociale de $1 sur $2 mentions dans la dernière heure.",
          "Социальный тон $1 по $2 упоминаниям за последний час."] },
    { re: /^Position size is reduced by (\d+)% to reflect the disagreement above\. Paid feeds were served from cache, so this scan cost nothing\.$/,
      t: ["头寸规模已下调 $1%，以反映上述分歧。付费数据源来自缓存，因此本次扫描零成本。",
          "El tamaño de la posición se reduce un $1% para reflejar el desacuerdo anterior. Los feeds de pago se sirvieron desde caché, por lo que este escaneo no costó nada.",
          "ऊपर के मतभेद को दर्शाने के लिए पोज़िशन साइज़ $1% घटाया गया। भुगतान वाले फ़ीड कैश से मिले, इसलिए इस स्कैन की कोई लागत नहीं।",
          "تم تقليل حجم المركز بنسبة $1% ليعكس الخلاف أعلاه. قُدِّمت الخلاصات المدفوعة من الذاكرة المؤقتة، لذا لم يكلّف هذا الفحص شيئًا.",
          "O tamanho da posição foi reduzido em $1% para refletir a discordância acima. Os feeds pagos foram servidos da cache, por isso esta análise não custou nada.",
          "La taille de la position est réduite de $1% pour refléter le désaccord ci-dessus. Les flux payants ont été servis depuis le cache, donc cette analyse n'a rien coûté.",
          "Размер позиции уменьшен на $1%, чтобы отразить разногласие выше. Платные потоки отданы из кэша, поэтому это сканирование ничего не стоило."] },
    { re: /^Ledger anchored at height (\d+) \(checkpoint #(\d+)\)\.$/,
      t: ["账本已在高度 $1 锚定（检查点 #$2）。",
          "Libro anclado a la altura $1 (checkpoint #$2).",
          "बहीखाता ऊँचाई $1 पर एंकर (चेकपॉइंट #$2)।",
          "تم تثبيت الدفتر عند الارتفاع $1 (نقطة تفتيش #$2).",
          "Livro-razão ancorado na altura $1 (checkpoint #$2).",
          "Registre ancré à la hauteur $1 (point de contrôle #$2).",
          "Реестр закреплён на высоте $1 (контрольная точка #$2)."] },
    { re: /^The Guardian has frozen every module out at threat (\d+)\/100\. Nothing will open until conditions recover or you clear it from the Sentinel view\.$/,
      t: ["守护者已在威胁 $1/100 时冻结所有模块。在条件恢复或你从哨兵视图解除之前，不会开启任何持仓。",
          "El Guardián ha congelado todos los módulos con una amenaza de $1/100. No se abrirá nada hasta que las condiciones se recuperen o lo elimines desde la vista Sentinel.",
          "गार्डियन ने खतरे $1/100 पर सभी मॉड्यूल फ़्रीज़ कर दिए। स्थितियाँ सुधरने या आपके Sentinel व्यू से हटाने तक कुछ नहीं खुलेगा।",
          "جمّد الحارس كل الوحدات عند تهديد $1/100. لن يُفتح أي شيء حتى تتعافى الظروف أو تزيله من عرض الحارس.",
          "O Guardião congelou todos os módulos com uma ameaça de $1/100. Nada abrirá até as condições recuperarem ou você limpar na vista Sentinel.",
          "Le Gardien a gelé tous les modules à une menace de $1/100. Rien ne s'ouvrira tant que les conditions ne se rétablissent pas ou que vous ne l'effacez pas depuis la vue Sentinel.",
          "Страж заморозил все модули при угрозе $1/100. Ничего не откроется, пока условия не восстановятся или вы не снимете это во вкладке Sentinel."] },
    { re: /^Running every analyst on (.+)…$/,
      t: ["正在对 $1 运行全部分析师…", "Ejecutando todos los analistas en $1…", "$1 पर सभी विश्लेषक चला रहे हैं…", "تشغيل جميع المحللين على $1…", "A executar todos os analistas em $1…", "Exécution de tous les analystes sur $1…", "Запуск всех аналитиков по $1…"] },
    { re: /^The system has changed its mind on (\d+) positions you hold\.$/,
      t: ["系统改变了对你所持 $1 个持仓的判断。", "El sistema ha cambiado de opinión sobre $1 posiciones que mantienes.", "सिस्टम ने आपकी $1 होल्डिंग्स पर अपना विचार बदला है।", "غيّر النظام رأيه بشأن $1 مراكز تحتفظ بها.", "O sistema mudou de ideias sobre $1 posições que detém.", "Le système a changé d'avis sur $1 positions que vous détenez.", "Система изменила мнение по $1 вашим позициям."] },
    { re: /^Net ([\d,]+) units left exchanges over 4h across (\d+) large transfers — accumulation, not distribution\.$/,
      t: ["过去4小时，净额 $1 单位流出交易所，涉及 $2 笔大额转账——是吸筹，而非派发。",
          "$1 unidades netas salieron de los exchanges en 4h a través de $2 transferencias grandes: acumulación, no distribución.",
          "पिछले 4घं में $1 यूनिट शुद्ध रूप से एक्सचेंजों से बाहर गईं, $2 बड़े ट्रांसफ़र में — संचय, वितरण नहीं।",
          "خرجت $1 وحدة صافية من المنصات خلال 4 ساعات عبر $2 تحويلًا كبيرًا — تجميع لا توزيع.",
          "$1 unidades líquidas saíram das exchanges em 4h através de $2 transferências grandes — acumulação, não distribuição.",
          "$1 unités nettes ont quitté les plateformes en 4 h via $2 gros transferts — accumulation, pas distribution.",
          "$1 единиц нетто покинули биржи за 4 ч через $2 крупных перевода — накопление, а не распределение."] },
    { re: /^Net ([\d,]+) units moved onto exchanges across (\d+) large transfers — supply arriving at the sell side\.$/,
      t: ["净额 $1 单位转入交易所，涉及 $2 笔大额转账——供给正抵达卖方。",
          "$1 unidades netas se movieron a los exchanges a través de $2 transferencias grandes: oferta llegando al lado vendedor.",
          "$1 यूनिट शुद्ध रूप से एक्सचेंजों में आईं, $2 बड़े ट्रांसफ़र में — आपूर्ति बिक्री-पक्ष पर पहुँच रही है।",
          "انتقلت $1 وحدة صافية إلى المنصات عبر $2 تحويلًا كبيرًا — المعروض يصل إلى جانب البيع.",
          "$1 unidades líquidas moveram-se para as exchanges através de $2 transferências grandes — oferta a chegar ao lado vendedor.",
          "$1 unités nettes se sont déplacées vers les plateformes via $2 gros transferts — l'offre arrive côté vente.",
          "$1 единиц нетто поступили на биржи через $2 крупных перевода — предложение приходит на сторону продажи."] },
    { re: /^\$([\d,]+) of deployable cash is under the \$([\d,]+) minimum\. Moving it would cost more in friction than it earns\.$/,
      t: ["可部署现金 $$1 低于 $$2 的最低门槛。移动它所花的摩擦成本会超过其收益。",
          "$$1 de efectivo desplegable está por debajo del mínimo de $$2. Moverlo costaría más en fricción de lo que rinde.",
          "तैनात-योग्य नकदी $$1, $$2 की न्यूनतम सीमा से कम है। इसे हिलाने की फ्रिक्शन लागत इसकी कमाई से अधिक होगी।",
          "النقد القابل للنشر $$1 أقل من الحد الأدنى $$2. تحريكه سيكلّف احتكاكًا أكثر مما يربح.",
          "$$1 de dinheiro implantável está abaixo do mínimo de $$2. Movê-lo custaria mais em fricção do que rende.",
          "$$1 de liquidités déployables est sous le minimum de $$2. Le déplacer coûterait plus en friction qu'il ne rapporte.",
          "$$1 развёртываемых средств ниже минимума $$2. Их перемещение обойдётся в трении дороже, чем принесёт."] },
    { re: /^(\d+) records · head (.+)$/,
      t: ["$1 条记录 · 头部 $2", "$1 registros · cabeza $2", "$1 रिकॉर्ड · हेड $2", "$1 سجلًا · الرأس $2", "$1 registos · cabeça $2", "$1 enregistrements · tête $2", "$1 записей · голова $2"] },
    { re: /^benched — (\d+) live calls wrong in a row$/,
      t: ["已停用——连续 $1 次实时判断错误", "en banca — $1 llamadas en vivo erradas seguidas", "बेंच किया गया — लगातार $1 लाइव कॉल गलत", "موقوف — $1 توقعات حية خاطئة متتالية", "em banco — $1 chamadas ao vivo erradas seguidas", "mis au banc — $1 appels en direct faux d'affilée", "отстранён — $1 ошибочных живых прогнозов подряд"] },
    { re: /^warming up \((\d+)\/(\d+) calls\)$/,
      t: ["预热中（$1/$2 次判断）", "calentando ($1/$2 llamadas)", "वॉर्म-अप ($1/$2 कॉल)", "قيد الإحماء ($1/$2 توقع)", "a aquecer ($1/$2 chamadas)", "en rodage ($1/$2 appels)", "разогрев ($1/$2 прогнозов)"] }
  ];
  function _trPattern(str, lang) {
    var idx = LANGS.indexOf(lang);
    if (idx < 0) return null;
    for (var i = 0; i < PATTERNS.length; i++) {
      var m = str.match(PATTERNS[i].re);
      if (m) {
        var tmpl = PATTERNS[i].t[idx];
        return tmpl ? tmpl.replace(/\$(\d)/g, function (_, d) { return m[+d] != null ? m[+d] : ""; }) : null;
      }
    }
    return null;
  }

  function _translateNode(node, lang) {
    var p = node.parentNode;
    if (!p) return;
    var tag = p.nodeName;
    if (tag === "SCRIPT" || tag === "STYLE") return;
    if (tag === "OPTION" && p.parentNode && p.parentNode.id === "lang-select") return; // keep language names native
    if (!_origText.has(node)) _origText.set(node, node.nodeValue);
    var orig = _origText.get(node);
    var trimmed = orig.replace(/\s+/g, " ").trim();
    if (!trimmed) return;
    if (lang === "en") { node.nodeValue = orig; return; }
    var t = _tr(trimmed, lang);
    if (!t) t = _trPattern(trimmed, lang);
    if (!t) { if (node.nodeValue !== orig) node.nodeValue = orig; return; }
    var lead = (orig.match(/^\s*/) || [""])[0];
    var trail = (orig.match(/\s*$/) || [""])[0];
    var next = lead + t + trail;
    if (node.nodeValue !== next) node.nodeValue = next;
  }

  function _translateAttrs(el, lang) {
    var store = _origAttr.get(el);
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i];
      if (!el.hasAttribute || !el.hasAttribute(a)) continue;
      if (!store) { store = {}; _origAttr.set(el, store); }
      if (!(a in store)) store[a] = el.getAttribute(a);
      var orig = store[a];
      if (lang === "en") { el.setAttribute(a, orig); continue; }
      var t = _trAttr(orig.trim(), lang);
      el.setAttribute(a, t || orig);
    }
  }

  function _walk(root, lang) {
    if (root.nodeType === 3) { _translateNode(root, lang); return; }
    if (root.nodeType !== 1) return;
    if (root.nodeName === "SCRIPT" || root.nodeName === "STYLE") return;
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var nodes = [], n;
    while ((n = tw.nextNode())) nodes.push(n);
    for (var i = 0; i < nodes.length; i++) _translateNode(nodes[i], lang);
    var els = root.querySelectorAll ? root.querySelectorAll("[placeholder],[title],[aria-label]") : [];
    for (var j = 0; j < els.length; j++) _translateAttrs(els[j], lang);
    if (root.hasAttribute && (root.hasAttribute("placeholder") || root.hasAttribute("title") || root.hasAttribute("aria-label")))
      _translateAttrs(root, lang);
  }

  var _pending = false;
  function _scheduleWalk() {
    if (window.currentLang === "en" || _pending) return;
    _pending = true;
    setTimeout(function () { _pending = false; _walk(document.body, window.currentLang); }, 60);
  }

  function _ensureObserver() {
    if (_observer) return;
    _observer = new MutationObserver(function () { _scheduleWalk(); });
    _observer.observe(document.body, { childList: true, subtree: true });
  }

  // Deterministic hook the app can call after it renders (belt and suspenders
  // alongside the observer): re-applies the active language to the whole page.
  window.retranslate = function () {
    if (window.currentLang && window.currentLang !== "en") _walk(document.body, window.currentLang);
  };

  window.applyLang = function (lang) {
    if (lang !== "en" && LANGS.indexOf(lang) === -1) lang = "en";
    window.currentLang = lang;
    _walk(document.body, lang);
    document.documentElement.setAttribute("lang", lang);
    try { localStorage.setItem("gb_lang", lang); } catch (e) { /* ignore */ }
    var sel = document.getElementById("lang-select");
    if (sel && sel.value !== lang) sel.value = lang;
    _ensureObserver();
    // Safety net: while a non-English language is active, re-apply on a slow
    // cadence so panels the app fetches and renders after a tab opens (which may
    // land after the render hook fired) are always caught. No-op in English, and
    // the change-guard above means unchanged nodes are never rewritten.
    if (!window.__i18nTimer) {
      window.__i18nTimer = setInterval(function () {
        if (window.currentLang && window.currentLang !== "en") _walk(document.body, window.currentLang);
      }, 1000);
    }
  };

  function initLang() {
    var saved = "en";
    try { saved = localStorage.getItem("gb_lang") || "en"; } catch (e) { /* ignore */ }
    var sel = document.getElementById("lang-select");
    if (sel) sel.addEventListener("change", function () { window.applyLang(sel.value); });
    window.applyLang(saved);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initLang);
  else initLang();
})();
