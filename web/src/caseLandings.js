export const caseLandings = {
  'ai-telegram-posts': {
    slug: 'ai-telegram-posts',
    kind: 'aiTelegramPosts',
    metrikaCase: 'ai_telegram_posts',
    navLabelKey: 'public.nav.cases.aiTelegramPosts',
  },
  'telegram-to-max': {
    slug: 'telegram-to-max',
    metrikaCase: 'telegram_to_max',
    markets: ['ru'],
    requiredPlatforms: ['max'],
    navLabelKey: 'public.nav.cases.telegramToMax',
    eyebrowKey: 'case.telegramToMax.eyebrow',
    titleKey: 'case.telegramToMax.title',
    subtitleKey: 'case.telegramToMax.subtitle',
    primaryCtaKey: 'case.telegramToMax.primaryCta',
    secondaryCtaKey: 'case.telegramToMax.secondaryCta',
    qualifiedFor: [
      'case.telegramToMax.qualifiedFor.1',
      'case.telegramToMax.qualifiedFor.2',
      'case.telegramToMax.qualifiedFor.3',
      'case.telegramToMax.qualifiedFor.4',
    ],
    problem: [
      'case.telegramToMax.problem.1',
      'case.telegramToMax.problem.2',
      'case.telegramToMax.problem.3',
    ],
    solution: [
      'case.telegramToMax.solution.1',
      'case.telegramToMax.solution.2',
      'case.telegramToMax.solution.3',
      'case.telegramToMax.solution.4',
    ],
    scenarios: [
      {
        titleKey: 'case.telegramToMax.scenario.1.title',
        textKey: 'case.telegramToMax.scenario.1.text',
        nextStepKey: 'case.telegramToMax.scenario.1.nextStep',
      },
      {
        titleKey: 'case.telegramToMax.scenario.2.title',
        textKey: 'case.telegramToMax.scenario.2.text',
        nextStepKey: 'case.telegramToMax.scenario.2.nextStep',
      },
      {
        titleKey: 'case.telegramToMax.scenario.3.title',
        textKey: 'case.telegramToMax.scenario.3.text',
        nextStepKey: 'case.telegramToMax.scenario.3.nextStep',
      },
      {
        titleKey: 'case.telegramToMax.scenario.4.title',
        textKey: 'case.telegramToMax.scenario.4.text',
        nextStepKey: 'case.telegramToMax.scenario.4.nextStep',
      },
    ],
    supported: [
      'case.telegramToMax.supported.1',
      'case.telegramToMax.supported.2',
      'case.telegramToMax.supported.3',
      'case.telegramToMax.supported.4',
    ],
    notPromised: [
      'case.telegramToMax.notPromised.1',
      'case.telegramToMax.notPromised.2',
      'case.telegramToMax.notPromised.3',
      'case.telegramToMax.notPromised.4',
    ],
    faq: [
      {
        questionKey: 'case.telegramToMax.faq.1.question',
        answerKey: 'case.telegramToMax.faq.1.answer',
      },
      {
        questionKey: 'case.telegramToMax.faq.2.question',
        answerKey: 'case.telegramToMax.faq.2.answer',
      },
      {
        questionKey: 'case.telegramToMax.faq.3.question',
        answerKey: 'case.telegramToMax.faq.3.answer',
      },
    ],
  },
  'multi-platform-publishing': {
    slug: 'multi-platform-publishing',
    metrikaCase: 'multi_platform_publishing',
    markets: ['io'],
    requiredPlatforms: ['x', 'linkedin'],
    navLabelKey: 'public.nav.cases.multiPlatform',
    ctaMode: 'compose',
    flow: {
      sourceName: 'Postbridge',
      sourceTextKey: 'case.multiPlatform.flow.sourceText',
      destinationName: 'LinkedIn · X · Bluesky · Mastodon',
      destinationTextKey: 'case.multiPlatform.flow.destinationText',
      ariaKey: 'case.multiPlatform.flow.aria',
    },
    sectionKeys: {
      scenariosTitle: 'case.multiPlatform.sections.scenariosTitle',
      scenariosAria: 'case.multiPlatform.sections.scenariosAria',
      proofTitle: 'case.multiPlatform.sections.proofTitle',
      notPromisedTitle: 'case.multiPlatform.sections.notPromisedTitle',
      ctaTitle: 'case.multiPlatform.sections.ctaTitle',
      ctaText: 'case.multiPlatform.sections.ctaText',
    },
    eyebrowKey: 'case.multiPlatform.eyebrow',
    titleKey: 'case.multiPlatform.title',
    subtitleKey: 'case.multiPlatform.subtitle',
    primaryCtaKey: 'case.multiPlatform.primaryCta',
    secondaryCtaKey: 'case.multiPlatform.secondaryCta',
    qualifiedFor: [1, 2, 3, 4].map((number) => `case.multiPlatform.qualifiedFor.${number}`),
    problem: [1, 2, 3].map((number) => `case.multiPlatform.problem.${number}`),
    solution: [1, 2, 3, 4].map((number) => `case.multiPlatform.solution.${number}`),
    scenarios: [1, 2, 3, 4].map((number) => ({
      titleKey: `case.multiPlatform.scenario.${number}.title`,
      textKey: `case.multiPlatform.scenario.${number}.text`,
      nextStepKey: `case.multiPlatform.scenario.${number}.nextStep`,
    })),
    supported: [1, 2, 3, 4].map((number) => `case.multiPlatform.supported.${number}`),
    notPromised: [1, 2, 3, 4].map((number) => `case.multiPlatform.notPromised.${number}`),
    faq: [1, 2, 3].map((number) => ({
      questionKey: `case.multiPlatform.faq.${number}.question`,
      answerKey: `case.multiPlatform.faq.${number}.answer`,
    })),
  },
  'chatgpt-social-publishing': {
    slug: 'chatgpt-social-publishing',
    metrikaCase: 'chatgpt_social_publishing',
    markets: ['io'],
    navLabelKey: 'public.nav.cases.chatgptPublishing',
    ctaMode: 'mcp',
    flow: {
      sourceName: 'ChatGPT',
      sourceTextKey: 'case.chatgptPublishing.flow.sourceText',
      destinationName: 'Postbridge channels',
      destinationTextKey: 'case.chatgptPublishing.flow.destinationText',
      ariaKey: 'case.chatgptPublishing.flow.aria',
    },
    sectionKeys: {
      scenariosTitle: 'case.chatgptPublishing.sections.scenariosTitle',
      scenariosAria: 'case.chatgptPublishing.sections.scenariosAria',
      proofTitle: 'case.chatgptPublishing.sections.proofTitle',
      notPromisedTitle: 'case.chatgptPublishing.sections.notPromisedTitle',
      ctaTitle: 'case.chatgptPublishing.sections.ctaTitle',
      ctaText: 'case.chatgptPublishing.sections.ctaText',
    },
    eyebrowKey: 'case.chatgptPublishing.eyebrow',
    titleKey: 'case.chatgptPublishing.title',
    subtitleKey: 'case.chatgptPublishing.subtitle',
    primaryCtaKey: 'case.chatgptPublishing.primaryCta',
    secondaryCtaKey: 'case.chatgptPublishing.secondaryCta',
    qualifiedFor: [1, 2, 3, 4].map((number) => `case.chatgptPublishing.qualifiedFor.${number}`),
    problem: [1, 2, 3].map((number) => `case.chatgptPublishing.problem.${number}`),
    solution: [1, 2, 3, 4].map((number) => `case.chatgptPublishing.solution.${number}`),
    scenarios: [1, 2, 3, 4].map((number) => ({
      titleKey: `case.chatgptPublishing.scenario.${number}.title`,
      textKey: `case.chatgptPublishing.scenario.${number}.text`,
      nextStepKey: `case.chatgptPublishing.scenario.${number}.nextStep`,
    })),
    supported: [1, 2, 3, 4].map((number) => `case.chatgptPublishing.supported.${number}`),
    notPromised: [1, 2, 3, 4].map((number) => `case.chatgptPublishing.notPromised.${number}`),
    faq: [1, 2, 3].map((number) => ({
      questionKey: `case.chatgptPublishing.faq.${number}.question`,
      answerKey: `case.chatgptPublishing.faq.${number}.answer`,
    })),
  },
}

function normalizePlatformId(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return normalized === 'twitter' ? 'x' : normalized
}

const disabledPlatforms = new Set(
  String(import.meta.env.VITE_POSTBRIDGE_DISABLED_PLATFORMS || '')
    .split(/[;,]/)
    .map(normalizePlatformId)
    .filter(Boolean),
)

function currentPublicMarket() {
  const configured = String(import.meta.env.VITE_POSTBRIDGE_PUBLIC_MARKET || '').trim().toLowerCase()
  if (configured === 'io' || configured === 'ru') return configured
  if (typeof window === 'undefined') return null
  const hostname = window.location.hostname.toLowerCase()
  if (hostname === 'postbridge.io' || hostname.endsWith('.postbridge.io')) return 'io'
  if (hostname === 'postbridge.ru' || hostname.endsWith('.postbridge.ru')) return 'ru'
  return null
}

export function isCaseLandingAvailable(landing) {
  const market = currentPublicMarket()
  if (market && landing?.markets && !landing.markets.includes(market)) return false
  return (landing?.requiredPlatforms || []).every(
    (platform) => !disabledPlatforms.has(normalizePlatformId(platform)),
  )
}

export function listPublicCaseLandings() {
  return Object.values(caseLandings).filter(isCaseLandingAvailable)
}

export function getCaseLanding(slug) {
  const landing = caseLandings[slug] || null
  return isCaseLandingAvailable(landing) ? landing : null
}

export function translateCaseLanding(landing, t) {
  if (!landing) return null
  if (landing.kind === 'aiTelegramPosts') {
    return landing
  }
  return {
    ...landing,
    eyebrow: t(landing.eyebrowKey),
    title: t(landing.titleKey),
    subtitle: t(landing.subtitleKey),
    primaryCta: t(landing.primaryCtaKey),
    secondaryCta: t(landing.secondaryCtaKey),
    flow: landing.flow
      ? {
          ...landing.flow,
          sourceText: t(landing.flow.sourceTextKey),
          destinationText: t(landing.flow.destinationTextKey),
          aria: t(landing.flow.ariaKey),
        }
      : null,
    sections: landing.sectionKeys
      ? Object.fromEntries(
          Object.entries(landing.sectionKeys).map(([name, key]) => [name, t(key)]),
        )
      : null,
    qualifiedFor: landing.qualifiedFor.map((key) => t(key)),
    problem: landing.problem.map((key) => t(key)),
    solution: landing.solution.map((key) => t(key)),
    scenarios: landing.scenarios.map((scenario) => ({
      title: t(scenario.titleKey),
      text: t(scenario.textKey),
      nextStep: t(scenario.nextStepKey),
    })),
    supported: landing.supported.map((key) => t(key)),
    notPromised: landing.notPromised.map((key) => t(key)),
    faq: landing.faq.map((item) => ({
      question: t(item.questionKey),
      answer: t(item.answerKey),
    })),
  }
}
