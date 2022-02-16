---
documentclass: article
title: Infrastructure Management 
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
This document will contain the general information about our infrastructure which I have managed over the course of the internship.
As well as the methods we use the manage the infrastructure.

# Infrastructure
## Architecture
The current infrastructure consists over 5 services deployed using Docker-as-a-Service (DaaS) from DevDroplets.
There are 2 static websites, and 3 webapps.
All of them are single, independent images; where the database are included in the images.

The webapps consist of:


- A staging version (beta.obtrader.ml)
- A production version (obtrader.ml)
- A spin-off app (backtest.live) which is publically available.


The static sites consist of:

- The company site (ob-trading.nl)
- The product landing page (onebutton.trade)

All of the traffic to these endpoints are encrypted with SSL certificates automatically provided by the DaaS.
We use HTTP/1.1 for all internal communication, however the DaaS uses a reverse proxy so the end-user will get the more optimized HTTP/2.

### Pre-Staging
We also have a server locally which isn't publically accessable. This is a dual-processor 16-core 64GB RAM Intel server which I also setup.

It is accessable via a VPN-like system called ZeroTier, and is used to test updates to are project which require a real enviroment but which can't be deployed yet.

## Procedures
### Updating Services
To deploy new version of the apps & websites we use CircleCI to create docker images which are uploaded to a private registry.
Afterwards a webhook is used to notify the DaaS to update the images & re-deploy.

In the CircleCI dashboard approving a build for a service should be enough to start the entire deployment process to completion.

### Upgrading Resource Limits
There are limited resources we are allowed to use for each container, however upgrading is usually quite easy. 
We notify the DaaS and they will increase the limits & price automatically.

If we need more resources then they can provide they will need to migrate the service to a new server which can take a bit more time.

# Reflection
Overall the infrastructure of the product is pretty easy to manage after setting up the deployment process as most of the infrastructure is managed by the DaaS.
Setting up the deployment did require a migration in CI, we originally used GitLab-CI but they wanted to move to Github so we needed to recreate it with something available on Github.

