---
documentclass: article
title: CircleCI Migration 
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
At the start of the semester we needed to migrate the CI from GitLab-CI to CircleCI, this was necessary because we switched VCS platforms (GitLab -> Github).
As well as adding Jira & Slack integration.

Since all of the CIs have their own propietary format it took a while to convert fully, this document shows the transition steps.


# CircleCI Migration
## Analysing the Original config
I started with the config we had from gitlab.
This showed a pretty straight-forward build process (See [Appendix A](#Appendices).

It starts with the images it should use to run the steps.
And then just runs them in a shell.

It first builds a base-image which all other images can use (this is cached for future runs).
Then it builds the images with `docker build` and then pushes them with `docker push`
Afterwards if everything succeeded it will call the webhooks on the DaaS platform to notify them to update the deployed services.

## Recreating in CircleCI
CircleCI didn't support the caching feature we had used with GitLab and recreating all of the images would take to long, so we split them up into their services.
This still used a base image but without the code being included.

After debugging CircleCIs confusing error messages I got that working. Then I needed to add a approval process as it would now run all of the services each commit.
This also was pretty confusing but I got it working after splitting the build-steps up further.

After verifying the deployment worked I added the webhooks adn the recreating was finished.

## Adding Integrations
For the new CI we also wanted to add Slack & Jira integrations, which CircleCI has a specific module for in their documentation (called Orbs).
The slack module worked fine first try, however the Jira integration was not working and there was no way to debug it.

Eventually we had a freelancer which had experience with Jira do the automation using regular Jira API and Bash.

## Adding static websites
Later on in the project we also had to add a few static website, which required new Docker images. I used [cttpd](https://git.devdroplets.com/root/cpp-http) to create a efficient/extendable webserver.

Then in each static site repository I just added a small `main.cpp` which contains the cttpd code to run.

# Reflection
The transition took a while and CircleCIs documentation is lacking at best, but the new integrations are pretty nice.
I think we could've looked at our options for different CIs as CircleCIs offering seems very immature.

And in the end the system works pretty well, and it's easy to use for the entire team.

# Appendices
## Appendix A
```yaml
image: docker:latest

services:
  - docker:18.09.7-dind
      
stages: [build]

build-and-push:
  before_script: [docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY]
  stage: build
  when: manual
  script: 
    - docker build -t obtrader-base:latest --build-arg GIT_CREDS=${GIT_CREDS} - < Dockerfile.base
    - docker build --no-cache -t $CI_REGISTRY_IMAGE/obtrader:latest - < Dockerfile
    - docker build --no-cache -t $CI_REGISTRY_IMAGE/obtrader:dev - < Dockerfile.dev
    - docker build --no-cache -t $CI_REGISTRY_IMAGE/backtest:latest - < Dockerfile.backtest
    - docker push $CI_REGISTRY_IMAGE/obtrader:dev
    - docker push $CI_REGISTRY_IMAGE/obtrader:latest
    - docker push $CI_REGISTRY_IMAGE/backtest:latest
    - apk add curl
    - curl -X POST $PORTAINER_HOOK_APP
    - curl -X POST $PORTAINER_HOOK_DEV
    - curl -X POST $PORTAINER_HOOK_BACKTEST
```

