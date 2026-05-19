export const caseLandings = {
  'ai-telegram-posts': {
    slug: 'ai-telegram-posts',
    kind: 'aiTelegramPosts',
    metrikaCase: 'ai_telegram_posts',
  },
  'telegram-to-max': {
    slug: 'telegram-to-max',
    metrikaCase: 'telegram_to_max',
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
}

export function getCaseLanding(slug) {
  return caseLandings[slug] || null
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
