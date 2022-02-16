---
documentclass: article
title: API Specifications Rapport
author: Ferris Kwaijtaal
date: \today
output:
    pdf_document:
        toc: false
        number_sections: true
geometry: "left=3cm,right=3cm,top=2cm,bottom=2cm"
fontsize: 11pt
link-citations: true
urlcolor: blue
header-includes:
- \usepackage{xcolor}
- \usepackage[dvipsnames]{xcolor}
- \usepackage[style=alphabetic,citestyle=alphabetic,backend=biber]{biblatex}
- \usepackage{graphicx}
- \graphicspath{{./}}
---

\tableofcontents

# Introduction
We are using OpenAPIv3 with Swagger to document/specify our API. 
The API is used by the frontend in order to preform actions or update/retrieve data.

All endpoints use a JSON body and responds with JSON.

Currently our backend implements 16+ different endpoints (URI + Request Type).

## Viewing swagger UI
[https://git.fhict.nl/I404788/trading-bot/-/blob/develop/openapi.json](https://git.fhict.nl/I404788/trading-bot/-/blob/develop/openapi.json)



