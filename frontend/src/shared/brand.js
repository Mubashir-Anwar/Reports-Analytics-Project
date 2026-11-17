// The product name, in one place (also in ui/index.html, the HTML <title>s and backend/app/constants.py BRAND_NAME).
export const BRAND = {
  name: 'Folio Reports',
  first: 'Folio',
  second: 'Reports',
  tagline: 'Design · Publish · Deliver',
};

export const pageTitle = (part) => `${part} · ${BRAND.name}`;
