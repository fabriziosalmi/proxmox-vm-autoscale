import { defineConfig, type HeadConfig } from 'vitepress'

const BASE = '/proxmox-vm-autoscale/'
const ORIGIN = 'https://fabriziosalmi.github.io'
const SITE_URL = ORIGIN + BASE
const REPO = 'https://github.com/fabriziosalmi/proxmox-vm-autoscale'
const OG_IMAGE =
  'https://repository-images.githubusercontent.com/864497613/5d9ba1ce-1327-4f6a-a1ed-9a75a2382609'

const DESCRIPTION =
  'Threshold-based CPU and RAM autoscaling for Proxmox VE virtual machines. ' +
  'A systemd service that drives qm over SSH, with hotplug support, host safety ' +
  'limits and Gotify/SMTP notifications.'

/** Absolute URL for a page path emitted by VitePress (e.g. "guide/index.md"). */
function canonicalFor(relativePath: string): string {
  const path = relativePath
    .replace(/index\.md$/, '')
    .replace(/\.md$/, '.html')
  return SITE_URL + path
}

/**
 * Structured data describing the project. Emitted once, on every page, as a
 * single @graph so crawlers and answer engines can resolve the relationships
 * between the site, the software and its author without guessing.
 */
const jsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebSite',
      '@id': SITE_URL + '#website',
      url: SITE_URL,
      name: 'Proxmox VM Autoscale',
      description: DESCRIPTION,
      inLanguage: 'en',
      publisher: { '@id': SITE_URL + '#author' },
      license: 'https://opensource.org/licenses/MIT'
    },
    {
      '@type': 'Person',
      '@id': SITE_URL + '#author',
      name: 'Fabrizio Salmi',
      url: 'https://github.com/fabriziosalmi',
      sameAs: ['https://github.com/fabriziosalmi']
    },
    {
      '@type': 'SoftwareSourceCode',
      '@id': SITE_URL + '#source',
      name: 'Proxmox VM Autoscale',
      description: DESCRIPTION,
      codeRepository: REPO,
      programmingLanguage: 'Python',
      runtimePlatform: 'Python 3.10+',
      author: { '@id': SITE_URL + '#author' },
      license: 'https://opensource.org/licenses/MIT',
      isAccessibleForFree: true
    },
    {
      '@type': 'SoftwareApplication',
      '@id': SITE_URL + '#application',
      name: 'Proxmox VM Autoscale',
      description: DESCRIPTION,
      applicationCategory: 'DeveloperApplication',
      applicationSubCategory: 'Infrastructure automation',
      operatingSystem: 'Linux (Debian/Proxmox VE)',
      softwareRequirements: 'Python 3.10+, Proxmox VE 6.0+, SSH access',
      url: SITE_URL,
      downloadUrl: REPO + '/releases',
      softwareHelp: SITE_URL + 'guide/',
      image: OG_IMAGE,
      author: { '@id': SITE_URL + '#author' },
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'EUR' },
      license: 'https://opensource.org/licenses/MIT'
    }
  ]
}

export default defineConfig({
  lang: 'en-US',
  title: 'Proxmox VM Autoscale',
  description: DESCRIPTION,
  base: BASE,

  cleanUrls: false,
  lastUpdated: true,
  metaChunk: true,

  sitemap: {
    hostname: SITE_URL
  },

  head: [
    ['link', { rel: 'icon', href: BASE + 'favicon.svg', type: 'image/svg+xml' }],
    ['meta', { name: 'theme-color', content: '#e57000' }],
    ['meta', { name: 'author', content: 'Fabrizio Salmi' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Proxmox VM Autoscale' }],
    ['meta', { property: 'og:image', content: OG_IMAGE }],
    ['meta', { property: 'og:locale', content: 'en_US' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: OG_IMAGE }],
    ['script', { type: 'application/ld+json' }, JSON.stringify(jsonLd)]
  ],

  // Per-page canonical + Open Graph URL. Without these every page would
  // advertise the site root, which is what makes duplicate-content warnings
  // and wrong share previews show up.
  transformPageData(pageData) {
    const url = canonicalFor(pageData.relativePath)
    const head: HeadConfig[] = pageData.frontmatter.head ?? []
    head.push(
      ['link', { rel: 'canonical', href: url }],
      ['meta', { property: 'og:url', content: url }],
      [
        'meta',
        {
          property: 'og:title',
          content: pageData.frontmatter.title ?? pageData.title ?? 'Proxmox VM Autoscale'
        }
      ],
      [
        'meta',
        {
          property: 'og:description',
          content: pageData.frontmatter.description ?? pageData.description ?? DESCRIPTION
        }
      ]
    )
    pageData.frontmatter.head = head
  },

  themeConfig: {
    logo: '/favicon.svg',
    siteTitle: 'VM Autoscale',

    nav: [
      { text: 'Guide', link: '/guide/', activeMatch: '/guide/' },
      { text: 'Reference', link: '/reference/configuration', activeMatch: '/reference/' },
      { text: 'Security', link: '/security/', activeMatch: '/security/' },
      {
        text: 'v1.4.0',
        items: [
          { text: 'Changelog', link: '/reference/changelog' },
          { text: 'Releases', link: REPO + '/releases' },
          { text: 'Contributing', link: '/contributing' }
        ]
      }
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Getting started',
          items: [
            { text: 'Introduction', link: '/guide/' },
            { text: 'Installation', link: '/guide/installation' },
            { text: 'Your first scaling VM', link: '/guide/quick-start' }
          ]
        },
        {
          text: 'Core concepts',
          items: [
            { text: 'How scaling decisions are made', link: '/guide/how-it-works' },
            { text: 'Hotplug and NUMA', link: '/guide/hotplug' },
            { text: 'Host safety limits', link: '/guide/host-limits' }
          ]
        },
        {
          text: 'Features',
          items: [
            { text: 'Notifications', link: '/guide/notifications' },
            { text: 'Billing tracking', link: '/guide/billing' }
          ]
        },
        {
          text: 'Running it',
          items: [
            { text: 'Operations', link: '/guide/operations' },
            { text: 'Troubleshooting', link: '/guide/troubleshooting' },
            { text: 'FAQ', link: '/guide/faq' }
          ]
        }
      ],
      '/reference/': [
        {
          text: 'Reference',
          items: [
            { text: 'Configuration', link: '/reference/configuration' },
            { text: 'Architecture', link: '/reference/architecture' },
            { text: 'Python modules', link: '/reference/modules' },
            { text: 'Known limitations', link: '/reference/limitations' },
            { text: 'Changelog', link: '/reference/changelog' }
          ]
        }
      ],
      '/security/': [
        {
          text: 'Security',
          items: [
            { text: 'Threat model', link: '/security/' },
            { text: 'Hardening guide', link: '/security/hardening' },
            { text: 'Reporting a vulnerability', link: '/security/disclosure' }
          ]
        }
      ]
    },

    socialLinks: [{ icon: 'github', link: REPO }],

    editLink: {
      pattern: REPO + '/edit/main/docs/:path',
      text: 'Edit this page on GitHub'
    },

    outline: { level: [2, 3], label: 'On this page' },

    search: {
      provider: 'local',
      options: {
        detailedView: true
      }
    },

    footer: {
      message: [
        `Released under the <a href="${REPO}/blob/main/LICENSE">MIT License</a>`,
        `<a href="${BASE}privacy.html">Privacy</a>`,
        `<a href="${BASE}security/disclosure.html">Security</a>`,
        `<a href="${BASE}.well-known/security.txt">security.txt</a>`,
        `<a href="${BASE}llms.txt">llms.txt</a>`,
        `<a href="${BASE}sitemap.xml">Sitemap</a>`
      ].join(' · '),
      copyright: `Copyright © 2024–${new Date().getFullYear()} <a href="https://github.com/fabriziosalmi">Fabrizio Salmi</a>`
    },

    docFooter: { prev: 'Previous', next: 'Next' }
  },

  markdown: {
    lineNumbers: false,
    theme: { light: 'github-light', dark: 'github-dark' }
  }
})
