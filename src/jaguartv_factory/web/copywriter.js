(function () {
  const product = {
    name: "JaguarTV",
    site: "Jarg.top",
    cta: "Baixe em Jarg.top",
    promise: "TV ao vivo, esportes, filmes, series e entretenimento em um so app",
    audience: "brasileiros que querem assistir no celular, Android TV ou TV box",
  };

  const dictionary = [
    [/足球|比赛|球赛|体育|世界杯|巴西队|联赛|进球|球星/g, "futebol e esportes", "sports"],
    [/街头|街边|街区|街头足球|户外/g, "rua e cultura urbana", "street"],
    [/挑战|挑战赛|对决|PK|pk|Battle|battle/g, "desafio e disputa", "challenge"],
    [/直播|电视直播|频道|电视频道|电视|tv|TV/g, "TV ao vivo e canais online", "tv"],
    [/电影|影视|大片|影院/g, "filmes e cinema em casa", "movies"],
    [/电视剧|剧集|连续剧|短剧|美剧|韩剧/g, "series e novelas", "series"],
    [/综艺|娱乐|节目|真人秀/g, "entretenimento e programas", "entertainment"],
    [/安卓|Android|手机|移动|app|应用|下载|安装/g, "app Android facil de baixar", "android"],
    [/免费|省钱|划算|优惠|低价|便宜/g, "opcao acessivel e pratica", "value"],
    [/教程|激活|充值|续费|使用|教学/g, "ativacao e uso sem complicacao", "tutorial"],
    [/周末|今天|今晚|现在|马上|限时/g, "o momento certo para assistir hoje", "timing"],
    [/巴西|巴西人|葡语|葡萄牙语/g, "Brasil e publico brasileiro", "brazil"],
    [/音乐|歌曲|演唱会|舞台/g, "musica e shows", "music"],
    [/新闻|热点|爆料|趋势|热门/g, "assuntos em alta", "trend"],
    [/赚钱|副业|创业|营销|流量|转化/g, "crescimento e oportunidades", "business"],
    [/健身|减肥|健康|运动/g, "saude, rotina e bem-estar", "health"],
    [/学习|考试|教育|课程|知识/g, "aprendizado pratico", "education"],
    [/美食|餐厅|做饭|食谱/g, "comida e experiencias do dia a dia", "food"],
  ];

  const toneCopy = {
    viral: {
      prefix: "Chamou atencao agora",
      proof: "simples, direto e feito para quem nao quer perder tempo",
      action: "confira antes de passar para o proximo video",
    },
    trust: {
      prefix: "Recomendacao pratica",
      proof: "com uma mensagem clara, util e facil de entender",
      action: "veja com calma e escolha o melhor proximo passo",
    },
    urgent: {
      prefix: "Atencao para hoje",
      proof: "ideal para agir enquanto o assunto ainda esta quente",
      action: "aproveite agora e nao deixe para depois",
    },
    friendly: {
      prefix: "Dica de amigo",
      proof: "sem complicar, do jeito que a gente gosta",
      action: "salva isso e manda para quem tambem precisa",
    },
  };

  const platformLimits = {
    shorts: { name: "YouTube Shorts", max: 100, rhythm: "gancho rapido + CTA claro" },
    tiktok: { name: "TikTok", max: 220, rhythm: "curiosidade + comentario compartilhavel" },
    kwai: { name: "Kwai", max: 180, rhythm: "beneficio direto + linguagem popular" },
    facebook: { name: "Facebook Reels", max: 420, rhythm: "contexto + beneficio + link" },
    whatsapp: { name: "WhatsApp", max: 360, rhythm: "mensagem curta para encaminhar" },
    email: { name: "Email", max: 600, rhythm: "assunto + preview + CTA" },
    seo: { name: "SEO", max: 160, rhythm: "titulo pesquisavel + meta description" },
  };

  const form = document.querySelector("#copyForm");
  const topicInput = document.querySelector("#topicInput");
  const platformSelect = document.querySelector("#platformSelect");
  const toneSelect = document.querySelector("#toneSelect");
  const ctaInput = document.querySelector("#ctaInput");
  const variantCount = document.querySelector("#variantCount");
  const heatRange = document.querySelector("#heatRange");
  const heatValue = document.querySelector("#heatValue");
  const resultStack = document.querySelector("#resultStack");
  const emptyState = document.querySelector("#emptyState");
  const copyAllButton = document.querySelector("#copyAllButton");
  const resetButton = document.querySelector("#resetButton");
  const toastElement = document.querySelector("#toast");
  const submitButton = form?.querySelector("button[type='submit']");

  let latestPlainText = "";

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[character]));
  }

  function compact(value, fallback = "") {
    return String(value || fallback).replace(/\s+/g, " ").trim();
  }

  function detectTopics(input) {
    const found = [];
    dictionary.forEach(([pattern, label, category]) => {
      pattern.lastIndex = 0;
      if (pattern.test(input)) {
        found.push({ label, category });
      }
    });
    if (!found.length) {
      found.push({ label: "o tema que voce escolheu", category: "general" });
    }
    return found;
  }

  function unique(values) {
    return [...new Set(values.filter(Boolean))];
  }

  function topicPhrase(input, maxItems = 2) {
    const topics = detectTopics(input);
    const labels = unique(topics.map((topic) => topic.label)).slice(0, maxItems);
    if (labels.length === 1) return labels[0];
    if (labels.length === 2) return `${labels[0]} com ${labels[1]}`;
    return `${labels.slice(0, -1).join(", ")} e ${labels[labels.length - 1]}`;
  }

  function topicCategories(input) {
    return unique(detectTopics(input).map((topic) => topic.category));
  }

  function contentTopicPhrase(input) {
    const categories = topicCategories(input);
    const hasBrazil = categories.includes("brazil");
    const place = hasBrazil ? " no Brasil" : "";
    if (categories.includes("sports") && categories.includes("street") && categories.includes("challenge")) {
      return `desafio de futebol de rua${place}`;
    }
    if (categories.includes("sports") && categories.includes("street")) return `futebol de rua${place}`;
    if (categories.includes("sports") && categories.includes("challenge")) return `desafio de futebol${place}`;
    if (categories.includes("sports")) return hasBrazil ? "futebol brasileiro" : "futebol";
    if (categories.includes("music")) return hasBrazil ? "musica brasileira" : "musica e shows";
    if (categories.includes("health")) return "rotina de saude e bem-estar";
    if (categories.includes("education")) return "aprendizado pratico";
    if (categories.includes("food")) return "comida e experiencias do dia a dia";
    if (categories.includes("business")) return "crescimento e oportunidades";
    if (categories.includes("trend")) return "assunto em alta";
    return topicPhrase(input).replace("TV ao vivo e canais online", "o tema escolhido");
  }

  const zhCategoryLabels = {
    sports: "足球和体育直播",
    tv: "电视直播和在线频道",
    movies: "电影和家庭影院",
    series: "电视剧、剧集和连续剧",
    entertainment: "娱乐节目和综艺内容",
    android: "安卓应用下载和安装",
    value: "省钱、划算和实用选择",
    tutorial: "激活、充值和使用教程",
    timing: "今天、今晚或限时场景",
    brazil: "巴西用户和葡语市场",
    music: "音乐和演出内容",
    trend: "热点和热门话题",
    business: "营销、流量和增长机会",
    health: "健康、健身和日常习惯",
    education: "学习、考试和实用知识",
    food: "美食和日常体验",
    general: "你输入的主题",
  };

  function zhTopicPhrase(input, maxItems = 2) {
    const labels = unique(detectTopics(input).map((topic) => zhCategoryLabels[topic.category] || zhCategoryLabels.general)).slice(0, maxItems);
    if (labels.length === 1) return labels[0];
    if (labels.length === 2) return `${labels[0]} + ${labels[1]}`;
    return `${labels.slice(0, -1).join("、")}和${labels[labels.length - 1]}`;
  }

  function zhContentTopicPhrase(input) {
    const categories = topicCategories(input);
    const hasBrazil = categories.includes("brazil");
    const place = hasBrazil ? "巴西" : "";
    if (categories.includes("sports") && categories.includes("street") && categories.includes("challenge")) {
      return `${place}街头足球挑战`;
    }
    if (categories.includes("sports") && categories.includes("street")) return `${place}街头足球`;
    if (categories.includes("sports") && categories.includes("challenge")) return `${place}足球挑战`;
    if (categories.includes("sports")) return hasBrazil ? "巴西足球" : "足球";
    if (categories.includes("music")) return hasBrazil ? "巴西音乐" : "音乐和演出";
    if (categories.includes("health")) return "健康和日常习惯";
    if (categories.includes("education")) return "实用学习";
    if (categories.includes("food")) return "美食和日常体验";
    if (categories.includes("business")) return "增长和机会";
    if (categories.includes("trend")) return "热门话题";
    return zhTopicPhrase(input);
  }

  function productBenefit(input) {
    const categories = unique(detectTopics(input).map((topic) => topic.category));
    if (categories.includes("sports")) {
      return "acompanhar futebol, jogos e momentos importantes sem ficar pulando de link em link";
    }
    if (categories.includes("tutorial")) {
      return "baixar, ativar e comecar a assistir com um caminho mais simples";
    }
    if (categories.includes("movies") || categories.includes("series")) {
      return "ter filmes, series e canais de entretenimento reunidos no mesmo app";
    }
    if (categories.includes("android")) {
      return "usar no Android, celular ou TV box com uma experiencia direta";
    }
    return "assistir TV ao vivo, esportes e entretenimento em um so lugar";
  }

  function zhProductBenefit(input) {
    const categories = unique(detectTopics(input).map((topic) => topic.category));
    if (categories.includes("sports")) {
      return "不用到处找链接，也能跟上足球、比赛和重要体育时刻";
    }
    if (categories.includes("tutorial")) {
      return "用更简单的路径完成下载、激活并开始观看";
    }
    if (categories.includes("movies") || categories.includes("series")) {
      return "把电影、剧集和娱乐频道集中在同一个应用里";
    }
    if (categories.includes("android")) {
      return "在 Android 手机、Android TV 或 TV box 上直接使用";
    }
    return "把直播 TV、体育和娱乐内容集中到一个地方观看";
  }

  function genericBenefit(input) {
    const categories = topicCategories(input);
    if (categories.includes("business")) return "transformar atencao em acao sem parecer forçado";
    if (categories.includes("health")) return "criar vontade de comecar hoje com uma promessa simples";
    if (categories.includes("education")) return "mostrar um caminho facil para aprender sem travar";
    if (categories.includes("food")) return "despertar desejo rapido com uma cena facil de imaginar";
    if (categories.includes("trend")) return "entrar na conversa enquanto o assunto esta quente";
    return "pegar um tema comum e transformar em uma mensagem facil de compartilhar";
  }

  function zhGenericBenefit(input) {
    const categories = topicCategories(input);
    if (categories.includes("business")) return "把注意力转化成行动，同时避免硬广感";
    if (categories.includes("health")) return "用简单承诺让用户愿意今天就开始";
    if (categories.includes("education")) return "展示一个不容易卡住的学习路径";
    if (categories.includes("food")) return "用容易想象的场景快速激发兴趣";
    if (categories.includes("trend")) return "趁话题热度还在时加入讨论";
    return "把普通主题包装成更容易分享的表达";
  }

  function genericScene(input) {
    const categories = topicCategories(input);
    if (categories.includes("sports") && categories.includes("street") && categories.includes("challenge")) {
      return "a rua vira campo, a bola fica no pe e cada drible decide quem ganha respeito";
    }
    if (categories.includes("sports") && categories.includes("challenge")) {
      return "a disputa comeca simples, mas cada lance muda o clima do jogo";
    }
    if (categories.includes("sports")) {
      return "a bola rola, a torcida reage e o melhor lance vira conversa";
    }
    if (categories.includes("health")) return "o primeiro passo parece pequeno, mas muda a energia do dia";
    if (categories.includes("education")) return "uma explicacao direta transforma duvida em entendimento";
    if (categories.includes("food")) return "o cheiro, a textura e o primeiro prato ja contam a historia";
    if (categories.includes("music")) return "o ritmo cresce, a cena prende e o refrao fica na cabeca";
    if (categories.includes("business")) return "uma escolha simples mostra onde existe oportunidade real";
    if (categories.includes("trend")) return "o detalhe certo explica por que todo mundo esta comentando";
    return "uma cena clara mostra o ponto principal sem precisar explicar demais";
  }

  function ptTopicReference(topic) {
    if (/^desafio/.test(topic)) return "esse desafio";
    if (/^futebol/.test(topic)) return "esse futebol";
    if (/^rotina/.test(topic)) return "essa rotina";
    if (/^musica/.test(topic)) return "essa musica";
    if (/^comida/.test(topic)) return "essa experiencia";
    if (/^aprendizado/.test(topic)) return "esse aprendizado";
    if (/^crescimento|^assunto|^o tema/.test(topic)) return "esse tema";
    return `esse ${topic}`;
  }

  function ptTopicReferenceFrom(topic) {
    if (/^desafio/.test(topic)) return "desse desafio";
    if (/^futebol/.test(topic)) return "desse futebol";
    if (/^rotina/.test(topic)) return "dessa rotina";
    if (/^musica/.test(topic)) return "dessa musica";
    if (/^comida/.test(topic)) return "dessa experiencia";
    if (/^aprendizado/.test(topic)) return "desse aprendizado";
    if (/^crescimento|^assunto|^o tema/.test(topic)) return "desse tema";
    return `desse ${topic}`;
  }

  function zhGenericScene(input) {
    const categories = topicCategories(input);
    if (categories.includes("sports") && categories.includes("street") && categories.includes("challenge")) {
      return "街道变成球场，球在脚下，每一次过人都决定谁更有面子";
    }
    if (categories.includes("sports") && categories.includes("challenge")) {
      return "挑战开始得很简单，但每个动作都会改变比赛气氛";
    }
    if (categories.includes("sports")) return "球一动起来，观众就有反应，精彩动作会自然引发讨论";
    if (categories.includes("health")) return "第一步看起来很小，但会改变一天的状态";
    if (categories.includes("education")) return "一个直接解释能把困惑变成理解";
    if (categories.includes("food")) return "气味、口感和第一盘菜本身就是故事";
    if (categories.includes("music")) return "节奏推进、画面抓人，副歌更容易被记住";
    if (categories.includes("business")) return "用一个简单选择展示真实机会在哪里";
    if (categories.includes("trend")) return "抓住一个细节，说明为什么大家都在讨论";
    return "用一个清楚场景直接呈现主题，不需要过度解释";
  }

  function heatWord(heat) {
    if (heat >= 8) return "agora";
    if (heat >= 5) return "hoje";
    return "com calma";
  }

  function hashtags(mode, input, platform) {
    const categories = unique(detectTopics(input).map((topic) => topic.category));
    const base = mode === "tv"
      ? ["JaguarTV", "TVAoVivo", "Brasil", "Android"]
      : ["Brasil", "Conteudo", "Dica", "Viral"];
    const extras = [];
    if (categories.includes("sports")) extras.push("Futebol", "Esportes");
    if (categories.includes("street")) extras.push("FutebolDeRua", "Rua");
    if (categories.includes("challenge")) extras.push("Desafio");
    if (categories.includes("movies")) extras.push("Filmes");
    if (categories.includes("series")) extras.push("Series");
    if (categories.includes("music")) extras.push("Musica");
    if (categories.includes("business")) extras.push("Marketing", "Crescimento");
    if (platform === "tiktok") extras.push("ParaVoce");
    if (platform === "shorts") extras.push("Shorts");
    if (platform === "kwai") extras.push("KwaiBrasil");
    const orderedTags = mode === "tv" ? [...base, ...extras] : [...extras, ...base];
    return unique(orderedTags).slice(0, 8).map((tag) => `#${tag}`).join(" ");
  }

  function titleVariants(mode, input, tone, heat, count) {
    const topic = mode === "tv" ? topicPhrase(input) : contentTopicPhrase(input);
    const hot = heatWord(heat);
    const benefit = mode === "tv" ? productBenefit(input) : genericBenefit(input);
    const names = mode === "tv" ? ["JaguarTV", "Jarg.top", "TV no Android"] : ["essa ideia", "esse conteudo", "essa dica"];
    const topicRef = ptTopicReference(topic);
    const patterns = mode === "tv" ? [
      `Como ${benefit} ${hot}`,
      `${topic}: o jeito mais simples de assistir com ${names[0]}`,
      `Antes do proximo jogo, veja isso no ${names[0]}`,
      `TV ao vivo no Brasil: uma forma pratica de acompanhar ${topic}`,
      `Quer ${benefit}? Comece pelo ${names[1]}`,
      `O atalho para curtir ${topic} sem complicacao`,
      `${names[0]}: canais, jogos e entretenimento para hoje`,
      `Se voce usa Android, essa dica de TV ajuda muito`,
    ] : [
      `${capitalize(topic)}: quem leva a melhor nesse desafio?`,
      `O lance desse ${topic.replace(/^desafio de /, "desafio de ")} que merece replay`,
      `Quando ${topicRef} vira disputa de verdade`,
      `${capitalize(topic)} com clima de rua, habilidade e provocacao`,
      `Esse momento separa coragem de criatividade em ${topic}`,
      `Olha o detalhe que mudou tudo nesse ${topic.replace(/^desafio de /, "desafio de ")}`,
      `${capitalize(topic)}: voce tentaria esse lance?`,
      `A cena de ${topic} que faz todo mundo escolher um lado`,
    ];
    if (tone === "trust") patterns.reverse();
    if (tone === "urgent") patterns.unshift(`Nao deixe ${topic} passar batido ${hot}`);
    return patterns.slice(0, count);
  }

  function zhTitleVariants(mode, input, tone, heat, count) {
    const topic = mode === "tv" ? zhTopicPhrase(input) : zhContentTopicPhrase(input);
    const hot = heat >= 8 ? "现在" : heat >= 5 ? "今天" : "稳一点";
    const benefit = mode === "tv" ? zhProductBenefit(input) : zhGenericBenefit(input);
    const patterns = mode === "tv" ? [
      `如何${benefit}`,
      `${topic}：用 JaguarTV 更简单地观看`,
      `下一场比赛前，先看看 JaguarTV 这个观看方式`,
      `巴西直播 TV：用更实用的方式跟上${topic}`,
      `想要${benefit}？从 Jarg.top 开始`,
      `轻松享受${topic}的快捷方式`,
      `JaguarTV：今天就能看的频道、比赛和娱乐内容`,
      `如果你用 Android，这个 TV 应用提示很有帮助`,
    ] : [
      `${topic}：这个挑战谁能赢？`,
      `${topic}里值得回放的那个瞬间`,
      `当${topic}变成真正的对决`,
      `${topic}里的街头感、技术和挑衅感`,
      `这个${topic}瞬间，把勇气和创意分出来了`,
      `看清这个细节，${topic}的结果就变了`,
      `${topic}：你敢试这个动作吗？`,
      `让观众必须选边站的${topic}画面`,
    ];
    if (tone === "trust") patterns.reverse();
    if (tone === "urgent") patterns.unshift(`别让${topic}错过热度，${hot}就行动`);
    return patterns.slice(0, count);
  }

  function platformCopy(mode, input, platform, tone, heat, cta, count) {
    const topic = mode === "tv" ? topicPhrase(input) : contentTopicPhrase(input);
    const toneData = toneCopy[tone] || toneCopy.viral;
    const benefit = mode === "tv" ? productBenefit(input) : genericBenefit(input);
    const platformName = platformLimits[platform]?.name || "Social";
    const scene = genericScene(input);
    const topicRef = ptTopicReference(topic);
    const topicFrom = ptTopicReferenceFrom(topic);
    const base = mode === "tv"
      ? [
        `${toneData.prefix}: se voce quer ${benefit}, o ${product.name} foi feito para facilitar sua rotina. ${cta}.`,
        `Tem dia que a gente so quer abrir o app e assistir. Com ${product.name}, ${product.promise.toLowerCase()}. ${toneData.action}.`,
        `${topic} combina com uma experiencia simples: baixar, abrir e acompanhar no Android. Procure ${product.name} em ${product.site}.`,
        `Para quem ama entretenimento no Brasil: ${product.name} junta TV ao vivo, esportes e conteudo em uma experiencia direta. ${cta}.`,
        `Se o assunto e ${topic}, o melhor gancho e mostrar facilidade: menos busca, mais tempo assistindo. ${product.name} no ${product.site}.`,
        `Seu proximo conteudo pode vender uma promessa clara: assistir melhor, sem complicar. ${product.name} ajuda nessa historia.`,
        `Mostre o antes e depois: antes, procurar links; depois, abrir ${product.name} e encontrar canais, jogos e entretenimento.`,
        `Para Reels e Shorts, comece com a dor: "perdeu tempo procurando onde assistir?" Depois mostre ${product.name} e chame para ${product.site}.`,
      ]
      : [
        `${capitalize(topic)} na pratica: ${scene}. Quem voce acha que venceu essa?`,
        `A melhor parte ${topicFrom} e quando ninguem sabe o resultado ate o ultimo movimento. Eu assistiria de novo.`,
        `Tem desafio que parece brincadeira, mas vira disputa seria em segundos. ${capitalize(topicRef)} tem exatamente esse clima.`,
        `O detalhe ${topicFrom} esta no controle: calma, criatividade e uma provocacao na hora certa.`,
        `Quando a rua vira palco, ${topic} fica mais real. Escolhe um lado: tecnica ou ousadia?`,
        `Esse lance ${topicFrom} comecou simples e terminou com todo mundo olhando. Voce tentaria fazer igual?`,
        `Nada de producao perfeita: so energia, movimento e aquele momento em que a galera reage junto.`,
        `${capitalize(topic)} pede uma pergunta simples: foi habilidade pura ou sorte no momento certo?`,
      ];
    return base.slice(0, count).map((line) => {
      const suffix = platform === "whatsapp" ? "Me chama que eu te envio o passo a passo." : "";
      return `${line} ${suffix}`.trim().slice(0, platformLimits[platform]?.max || 420);
    }).map((line, index) => `${index + 1}. [${platformName}] ${line}`);
  }

  function zhPlatformCopy(mode, input, platform, tone, heat, cta, count) {
    const topic = mode === "tv" ? zhTopicPhrase(input) : zhContentTopicPhrase(input);
    const platformName = platformLimits[platform]?.name || "Social";
    const toneLabel = {
      viral: "强钩子",
      trust: "可信推荐",
      urgent: "限时行动",
      friendly: "朋友口吻",
    }[tone] || "强钩子";
    const heatLabel = heat >= 8 ? "行动感很强" : heat >= 5 ? "适合今天发布" : "语气更稳";
    const benefit = mode === "tv" ? zhProductBenefit(input) : zhGenericBenefit(input);
    const scene = zhGenericScene(input);
    const base = mode === "tv"
      ? [
        `用${toneLabel}开头：如果用户想${benefit}，JaguarTV 可以降低观看门槛。行动按钮是“${cta}”。`,
        `表达日常场景：用户只想打开应用就看内容，JaguarTV 把直播 TV、体育、电影和剧集放在一个应用里。`,
        `围绕${topic}强调简单体验：下载、打开，并在 Android 上观看，引导用户去 Jarg.top 找 JaguarTV。`,
        `面向巴西娱乐用户：突出直播 TV、体育和内容聚合，语气直接，适合${platformName}。`,
        `如果主题是${topic}，核心卖点是少搜索、多观看，并把品牌和 Jarg.top 露出清楚。`,
        `把文案包装成“观看更方便”的承诺，适合做短视频口播或图文说明。`,
        `用前后对比：以前到处找链接，现在打开 JaguarTV 找频道、比赛和娱乐内容。`,
        `短视频开头先说痛点：“还在浪费时间找哪里看？”然后展示 JaguarTV 和 Jarg.top。`,
      ]
      : [
        `${topic}的内容含义：${scene}。结尾用“你觉得谁赢了？”引导评论。`,
        `强调${topic}里最吸引人的地方：不到最后一个动作，大家都不知道结果。`,
        `把挑战写成从轻松玩笑变成认真对决的过程，突出现场感。`,
        `突出${topic}里的控制力、冷静、创意和刚好出现的挑衅感。`,
        `把街头场景当成舞台，让观众在“技术”和“胆量”之间选边。`,
        `描述一个从普通开场变成全场关注的动作，并问观众敢不敢尝试。`,
        `强调真实感：不追求完美制作，而是能量、动作和围观反应。`,
        `用一个简单判断题收尾：这是纯技术，还是抓住了刚好的运气？`,
      ];
    return base.slice(0, count).map((line, index) => `${index + 1}. [${platformName}] ${line}（热度：${heatLabel}）`);
  }

  function emailSequence(mode, input, cta) {
    const topic = mode === "tv" ? topicPhrase(input) : contentTopicPhrase(input);
    const scene = genericScene(input);
    if (mode === "tv") {
      return [
        {
          name: "Boas-vindas + promessa",
          subject: "TV ao vivo sem complicacao no Android",
          preview: `Veja como o ${product.name} ajuda voce a acompanhar esportes e entretenimento.`,
          body: `Oi! Se voce quer acompanhar ${topic} sem ficar procurando links toda hora, o ${product.name} pode deixar o caminho mais simples: TV ao vivo, esportes, filmes e series em um so app. Comece pelo ${product.site} e siga o passo a passo de instalacao.`,
          cta,
        },
        {
          name: "Objeção + tutorial",
          subject: "Como comecar em poucos passos",
          preview: "Baixar, abrir e testar nao precisa ser confuso.",
          body: `Muita gente trava na hora de instalar ou ativar um app de TV. Por isso, a melhor mensagem e direta: entre no ${product.site}, baixe o app, siga a orientacao e confirme se esta tudo certo antes do jogo ou programa que voce quer assistir.`,
          cta,
        },
        {
          name: "Conversão",
          subject: "Pronto para assistir hoje?",
          preview: `O ${product.name} reune canais, jogos e entretenimento para sua rotina.`,
          body: `Se voce quer uma opcao pratica para assistir no celular, Android TV ou TV box, teste o ${product.name}. A promessa precisa ser simples: menos procura, mais tempo assistindo o que voce gosta.`,
          cta,
        },
      ];
    }
    return [
      {
        name: "Abertura do tema",
        subject: `${capitalize(topic)}: olha esse momento`,
        preview: "Uma cena curta, direta e feita para puxar opiniao.",
        body: `Hoje o destaque e ${topic}: ${scene}. A pergunta e simples: voce ficaria do lado da tecnica ou da ousadia?`,
        cta: "Ver o momento",
      },
      {
        name: "Reacao",
        subject: `O detalhe que mudou ${topic}`,
        preview: "Nem sempre o melhor lance e o mais forte.",
        body: `O que chama atencao em ${topic} e o detalhe: um toque, uma pausa, uma provocacao e pronto, a cena muda de ritmo. Esse e o tipo de momento que merece replay.`,
        cta: "Assistir de novo",
      },
      {
        name: "Discussao",
        subject: "Agora e sua vez de escolher",
        preview: "Tecnica, coragem ou sorte: qual pesou mais?",
        body: `Depois desse ${topic}, fica a duvida: foi habilidade pura, coragem no momento certo ou sorte? Responde e marca quem teria coragem de tentar.`,
        cta: "Responder",
      },
    ];
  }

  function zhEmailSequence(mode, input, cta) {
    const topic = mode === "tv" ? zhTopicPhrase(input) : zhContentTopicPhrase(input);
    const scene = zhGenericScene(input);
    if (mode === "tv") {
      return [
        {
          name: "欢迎 + 承诺",
          subject: "Android 上更简单地看直播 TV",
          preview: "说明 JaguarTV 如何帮助用户观看体育和娱乐内容。",
          body: `如果用户想跟上${topic}，核心意思是：不用一直找链接，JaguarTV 把直播 TV、体育、电影和剧集放在一个应用里。邮件引导用户从 Jarg.top 开始并按照步骤安装。`,
          cta,
        },
        {
          name: "消除疑虑 + 教程",
          subject: "几步就能开始使用",
          preview: "下载、打开和测试不应该复杂。",
          body: "这封邮件解释很多人会卡在安装或激活，所以文案要直接：进入 Jarg.top，下载应用，按照指引操作，并在想看的比赛或节目开始前确认是否可用。",
          cta,
        },
        {
          name: "转化",
          subject: "今天准备好观看了吗？",
          preview: "JaguarTV 把频道、比赛和娱乐内容集中到日常使用里。",
          body: "面向手机、Android TV 或 TV box 用户，强调实用选择：少搜索，多观看自己喜欢的内容。",
          cta,
        },
      ];
    }
    return [
      {
        name: "主题开场",
        subject: `${topic}：看这个瞬间`,
        preview: "一段短、直接、适合引发观点的内容。",
        body: `这封邮件的意思是：今天重点是${topic}，${scene}。结尾让用户选择：你站技术，还是站胆量？`,
        cta: "查看这个瞬间",
      },
      {
        name: "反应",
        subject: `改变${topic}结果的细节`,
        preview: "最精彩的动作不一定是最用力的动作。",
        body: `这封邮件强调${topic}里的细节：一次触球、一个停顿、一次挑衅，整个画面节奏就改变了，值得回放。`,
        cta: "再看一遍",
      },
      {
        name: "讨论",
        subject: "现在轮到你选择",
        preview: "技术、勇气还是运气：哪个更关键？",
        body: `这封邮件引导讨论：看完${topic}后，让用户判断这是纯技术、关键时刻的勇气，还是刚好有运气，并鼓励标记朋友。`,
        cta: "参与回答",
      },
    ];
  }

  function seoPack(mode, input) {
    const topic = mode === "tv" ? topicPhrase(input) : contentTopicPhrase(input);
    if (mode === "tv") {
      return {
        title: `${product.name}: TV ao vivo, futebol e entretenimento no Android`,
        description: `Baixe o ${product.name} pelo ${product.site} e acompanhe TV ao vivo, esportes, filmes e series em uma experiencia simples para o publico brasileiro.`,
        keywords: ["TV ao vivo", "app Android TV", "futebol ao vivo", "JaguarTV", "Jarg.top", topic],
      };
    }
    return {
      title: `${capitalize(topic)}: lances, desafio e reacoes`,
      description: `Veja ${topic} em uma cena direta, com energia, disputa e uma pergunta para quem gosta de comentar o melhor momento.`,
      keywords: [topic, "desafio", "reacao", "conteudo brasileiro", "video curto"],
    };
  }

  function zhSeoPack(mode, input) {
    const topic = mode === "tv" ? zhTopicPhrase(input) : zhContentTopicPhrase(input);
    if (mode === "tv") {
      return {
        title: "JaguarTV：Android 上的直播 TV、足球和娱乐内容",
        description: "通过 Jarg.top 下载 JaguarTV，面向巴西用户宣传直播 TV、体育、电影和剧集的一体化观看体验。",
        keywords: ["直播 TV", "Android TV 应用", "足球直播", "JaguarTV", "Jarg.top", topic],
      };
    }
    return {
      title: `${topic}：精彩动作、挑战和观众反应`,
      description: `围绕${topic}生成可直接发布的巴西葡语内容，突出现场感、对决感和评论互动。`,
      keywords: [topic, "挑战", "反应", "巴西内容", "短视频"],
    };
  }

  function strategy(mode, input, platform, tone, heat) {
    const topic = mode === "tv" ? topicPhrase(input) : contentTopicPhrase(input);
    const platformData = platformLimits[platform] || platformLimits.shorts;
    const toneData = toneCopy[tone] || toneCopy.viral;
    const goal = mode === "tv"
      ? `levar o usuario brasileiro a baixar ou testar ${product.name}`
      : `criar um conteudo direto sobre ${topic}, com cena, opiniao e comentario`;
    return [
      `Tema principal em pt-BR: ${topic}`,
      `Objetivo: ${goal}`,
      `Canal: ${platformData.name} (${platformData.rhythm})`,
      `Tom: ${toneData.proof}`,
      `Forca do gancho: ${heat}/10`,
    ].join("\n");
  }

  function zhStrategy(mode, input, platform, tone, heat) {
    const topic = mode === "tv" ? zhTopicPhrase(input) : zhContentTopicPhrase(input);
    const platformData = platformLimits[platform] || platformLimits.shorts;
    const toneLabel = {
      viral: "爆款强钩子",
      trust: "可信赖推荐",
      urgent: "限时行动感",
      friendly: "朋友口吻",
    }[tone] || "爆款强钩子";
    const goal = mode === "tv"
      ? "引导巴西用户下载或测试 JaguarTV"
      : `直接生成围绕${topic}的内容文案，用场景、观点和评论互动带动观看`;
    return [
      `中文审核主题：${topic}`,
      `目标：${goal}`,
      `渠道：${platformData.name}，节奏是${platformData.rhythm}`,
      `语气：${toneLabel}`,
      `钩子强度：${heat}/10`,
    ].join("\n");
  }

  function capitalize(value) {
    return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : value;
  }

  function generateCopy(input, options) {
    const cleanInput = compact(input);
    const mode = options.mode || "tv";
    const platform = options.platform || "shorts";
    const tone = options.tone || "viral";
    const heat = Number(options.heat || 7);
    const count = Number(options.count || 5);
    const cta = compact(options.cta, mode === "tv" ? product.cta : "Saiba mais");

    const titles = titleVariants(mode, cleanInput, tone, heat, count);
    const captions = platformCopy(mode, cleanInput, platform, tone, heat, cta, count);
    const emails = emailSequence(mode, cleanInput, cta);
    const seo = seoPack(mode, cleanInput);
    const tags = hashtags(mode, cleanInput, platform);
    const zhAudit = {
      strategy: zhStrategy(mode, cleanInput, platform, tone, heat),
      titles: zhTitleVariants(mode, cleanInput, tone, heat, count),
      captions: zhPlatformCopy(mode, cleanInput, platform, tone, heat, cta, count),
      cta: mode === "tv"
        ? `行动按钮意思：引导用户执行“${cta}”，通常是去 Jarg.top 下载或查看 JaguarTV。`
        : `行动按钮意思：引导用户执行“${cta}”，不包含 TV 产品信息。`,
      hashtags: mode === "tv"
        ? "标签用于强化 JaguarTV、直播 TV、巴西、Android 和相关主题识别。"
        : "标签用于强化主题、内容类型和平台推荐识别，不加入任何 TV 产品品牌或下载站点。",
      emails: zhEmailSequence(mode, cleanInput, cta),
      seo: zhSeoPack(mode, cleanInput),
    };
    return {
      mode,
      strategy: strategy(mode, cleanInput, platform, tone, heat),
      titles,
      captions,
      cta,
      hashtags: tags,
      emails,
      seo,
      zhAudit,
      note: mode === "tv"
        ? "Antes de publicar, revise claims de canais, direitos de conteudo, preco e disponibilidade por regiao."
        : "Para melhorar performance, teste 2-3 hooks e compare retencao, comentarios e cliques.",
    };
  }

  async function generateCopyWithAI(input, options) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch("/api/copywriter/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input, ...options }),
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.error || `AI request failed (${response.status})`);
      }
      if (!payload.result || typeof payload.result !== "object") {
        throw new Error("AI response is empty");
      }
      return payload.result;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function plainText(result) {
    const emailText = result.emails.map((item, index) => [
      `Email ${index + 1}: ${item.name}`,
      `Assunto: ${item.subject}`,
      `Preview: ${item.preview}`,
      `Corpo: ${item.body}`,
      `CTA: ${item.cta}`,
    ].join("\n")).join("\n\n");
    const zhAuditText = formatZhAudit(result);
    return [
      "ESTRATEGIA",
      result.strategy,
      "",
      "TITULOS / GANCHOS",
      result.titles.map((item, index) => `${index + 1}. ${item}`).join("\n"),
      "",
      "LEGENDAS",
      result.captions.join("\n"),
      "",
      "CTA",
      result.cta,
      "",
      "HASHTAGS",
      result.hashtags,
      "",
      "EMAILS",
      emailText,
      "",
      "SEO",
      `Title: ${result.seo.title}`,
      `Description: ${result.seo.description}`,
      `Keywords: ${result.seo.keywords.join(", ")}`,
      "",
      "NOTA",
      result.note,
      result.source ? `Fonte: ${result.source}${result.model ? ` (${result.model})` : ""}` : "",
      "",
      "中文审核翻译",
      zhAuditText,
    ].join("\n");
  }

  function formatZhAudit(result) {
    const emailText = result.zhAudit.emails.map((item, index) => [
      `邮件 ${index + 1}：${item.name}`,
      `主题：${item.subject}`,
      `预览：${item.preview}`,
      `正文含义：${item.body}`,
      `CTA：${item.cta}`,
    ].join("\n")).join("\n\n");
    return [
      "策略摘要",
      result.zhAudit.strategy,
      "",
      "标题 / 开头钩子",
      result.zhAudit.titles.map((item, index) => `${index + 1}. ${item}`).join("\n"),
      "",
      "平台正文含义",
      result.zhAudit.captions.join("\n"),
      "",
      "CTA 与标签含义",
      `${result.zhAudit.cta}\n${result.zhAudit.hashtags}`,
      "",
      "邮件跟进序列含义",
      emailText,
      "",
      "SEO 文案含义",
      `标题：${result.zhAudit.seo.title}`,
      `描述：${result.zhAudit.seo.description}`,
      `关键词：${result.zhAudit.seo.keywords.join("，")}`,
    ].join("\n");
  }

  function card(title, content, kind = "pre") {
    const body = kind === "list"
      ? `<ol class="variant-list">${content.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>`
      : `<pre>${escapeHtml(content)}</pre>`;
    return `
      <article class="result-card">
        <header>
          <strong>${escapeHtml(title)}</strong>
          <button class="copy-button" type="button" data-copy="${escapeHtml(typeof content === "string" ? content : content.join("\n"))}">复制</button>
        </header>
        ${body}
      </article>
    `;
  }

  function render(result) {
    const emailText = result.emails.map((item, index) => [
      `Email ${index + 1}: ${item.name}`,
      `Assunto: ${item.subject}`,
      `Preview: ${item.preview}`,
      `Corpo: ${item.body}`,
      `CTA: ${item.cta}`,
    ].join("\n")).join("\n\n");
    const seoText = [
      `Title: ${result.seo.title}`,
      `Meta description: ${result.seo.description}`,
      `Keywords: ${result.seo.keywords.join(", ")}`,
    ].join("\n");
    const zhAuditText = formatZhAudit(result);
    const sourceText = result.source
      ? `来源：${result.source === "local" ? "本地模板" : "AI 大模型"}${result.model ? ` (${result.model})` : ""}`
      : "";

    resultStack.innerHTML = [
      card("策略摘要", result.strategy),
      card("短视频标题 / 开头钩子", result.titles, "list"),
      card("平台正文", result.captions.join("\n")),
      card("CTA 与标签", `CTA: ${result.cta}\nHashtags: ${result.hashtags}`),
      card("邮件跟进序列", emailText),
      card("SEO 文案包", seoText),
      `<p class="quality-note">${escapeHtml([result.note, sourceText].filter(Boolean).join("\n"))}</p>`,
      card("中文审核翻译", zhAuditText),
    ].join("");
    latestPlainText = plainText(result);
    emptyState.hidden = true;
    resultStack.hidden = false;
    copyAllButton.disabled = false;
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      toast("已复制");
    } catch (error) {
      toast("复制失败，请手动选择文本", "error");
    }
  }

  function toast(message, type = "ok") {
    toastElement.textContent = message;
    toastElement.className = `toast show ${type === "error" ? "error" : ""}`;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => {
      toastElement.className = "toast";
    }, 2600);
  }

  function selectedMode() {
    return document.querySelector("input[name='mode']:checked")?.value || "tv";
  }

  function syncModeOptions() {
    document.querySelectorAll(".mode-option").forEach((option) => {
      option.classList.toggle("active", option.querySelector("input")?.checked);
    });
    if (selectedMode() === "tv" && (!ctaInput.value.trim() || ctaInput.value === "Saiba mais")) {
      ctaInput.value = product.cta;
    } else if (selectedMode() === "generic" && (!ctaInput.value.trim() || ctaInput.value === product.cta)) {
      ctaInput.value = "Saiba mais";
    }
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = topicInput.value.trim();
    if (!input) {
      topicInput.focus();
      toast("请先输入中文关键词或主题", "error");
      return;
    }
    const options = {
      mode: selectedMode(),
      platform: platformSelect.value,
      tone: toneSelect.value,
      heat: heatRange.value,
      count: variantCount.value,
      cta: ctaInput.value,
    };
    const originalSubmitText = submitButton?.textContent || "";
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "AI 生成中...";
    }
    let result;
    try {
      result = await generateCopyWithAI(input, options);
      toast("AI 已生成");
    } catch (error) {
      result = generateCopy(input, options);
      result.source = "local";
      result.model = "fallback";
      result.note = `${result.note}\nAI 暂不可用，已使用本地模板：${error.message}`;
      toast("AI 暂不可用，已使用本地模板", "error");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = originalSubmitText || "生成巴西葡语文案";
      }
    }
    render(result);
  });

  document.querySelectorAll("input[name='mode']").forEach((input) => {
    input.addEventListener("change", syncModeOptions);
  });

  heatRange?.addEventListener("input", () => {
    heatValue.textContent = heatRange.value;
  });

  resultStack?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (button) copyText(button.dataset.copy || "");
  });

  copyAllButton?.addEventListener("click", () => {
    if (latestPlainText) copyText(latestPlainText);
  });

  function applyInitialParams() {
    const params = new URLSearchParams(window.location.search);
    const topic = compact(params.get("topic"));
    if (topic && topicInput) topicInput.value = topic.slice(0, Number(topicInput.maxLength || 420));
    const mode = compact(params.get("mode"));
    const modeInput = [...document.querySelectorAll("input[name='mode']")].find((input) => input.value === mode);
    if (modeInput) modeInput.checked = true;
    const platform = compact(params.get("platform"));
    if (platform && [...(platformSelect?.options || [])].some((option) => option.value === platform)) {
      platformSelect.value = platform;
    }
    const tone = compact(params.get("tone"));
    if (tone && [...(toneSelect?.options || [])].some((option) => option.value === tone)) {
      toneSelect.value = tone;
    }
    const cta = compact(params.get("cta"));
    if (cta && ctaInput) ctaInput.value = cta;
  }

  resetButton?.addEventListener("click", () => {
    form.reset();
    heatValue.textContent = heatRange.value;
    syncModeOptions();
    resultStack.innerHTML = "";
    resultStack.hidden = true;
    emptyState.hidden = false;
    copyAllButton.disabled = true;
    latestPlainText = "";
    topicInput.focus();
  });

  applyInitialParams();
  syncModeOptions();

  window.JaguarCopywriter = { generateCopy, detectTopics, topicPhrase };
  if (typeof module !== "undefined") {
    module.exports = { generateCopy, detectTopics, topicPhrase };
  }
}());
