import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

const isGitHubPages = process.env.DEPLOY_TARGET === 'github-pages';
const site = isGitHubPages
  ? 'https://aces40love.github.io'
  : 'https://mattkingphotography.com';

export default defineConfig({
  site,
  base: isGitHubPages ? '/matt-king-photography' : '/',
  output: 'static',
  integrations: [
    sitemap({
      filter: (page) => !page.endsWith('/thank-you/'),
    }),
  ],
  compressHTML: true,
  build: {
    inlineStylesheets: 'auto',
  },
  image: {
    responsiveStyles: true,
    layout: 'constrained',
  },
});
