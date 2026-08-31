import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://mattkingphotography.com',
  output: 'static',
  integrations: [
    sitemap({
      filter: (page) => page !== 'https://mattkingphotography.com/thank-you/',
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
