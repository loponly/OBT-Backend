---
documentclass: article
title: Development Server Setup 
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
During the project we got a new server in our office which was to be used with our project for AI training & Backend Testing.

I set it up to have the correct dependencies, be accessable from anywhere, and have a nice interface for developers.

# Setting up the Development server
We have a development server for the project to be used to test the project and train AI.

It is a dual-processor 16-core 64GB RAM Intel server with plenty of storage.

The server is setup to run [Alpine Linux (musl-libc)](https://www.alpinelinux.org/), with [miniforge](https://github.com/conda-forge/miniforge) & [code-server](https://github.com/cdr/code-server).
I chose Alpine Linux as it has a very small footprint and a sound package manager. Only disadvantage is that it uses `muslc` instead of `glibc` which means that pre-compiled packages might not be compatible.

I recompiled [Pytorch(v1.4.1 & 1.7.1) for Alpine](https://github.com/i404788/pytorch/releases) so it can be installed on other alpine systems with relative ease.
It had to be compiled with [TBB](https://github.com/oneapi-src/oneTBB) instead of [OMP](https://www.openmp.org/) because OMP didn't want to compile on Alpine. Also no CuDNN support as it is build with a binary blob.

After setting up the requirements to run our project [ZeroTier](https://www.zerotier.com/) was added which allows you to join a VPN(-ish) by which you to login to the server remotely.
ZeroTier allos you to also manage the connected devices by allowing/disallowing them to connect the the virtual network switch.


# Reflection
This task took quite a bit of time mainly because of the `muslc` requirements, however it was quite interesting to compile Pytorch from scratch.
While ZeroTier isn't always stable it worked quite well in this case.
The server hasn't been used too much even though I set it up with a user-friendly interface (code-server), probably because all of the developers have their own local instances.
But it has been useful in the training of AI.

