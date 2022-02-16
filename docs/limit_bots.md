---
documentclass: article
title: Creating Limit Bots 
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
For our "next-gen" bots we want to add [limit orders](https://www.investopedia.com/terms/l/limitorder.asp) to our interface so the AIs can more efficiently make trades. \
This required a lot of changes to our backend and a few changes to our AI structure.

# Limit Bots
Limit Bots are the next-gen bots we are creating allowing them to anticipate changes in the price and play to that.

This does make the training slightly more complicated as explained below.

Limit Bots will create a [limit orders](https://www.investopedia.com/terms/l/limitorder.asp) instead of a Market Order as the regular models do. \
However limit orders requires outputing a price at which to put the limit order as well as an expire time (otherwise it's not very useful).

## AI Interface
Previously our interface (the output of the model) would look like this: `[float, float, float]` which each indicate an action to take.
We used the argmax to get check which action should be used, and that value would also denote the amount to use for that action.

Now the interface is: `([float, float], [float, float])`, the the inner part has `price` and `amount` to buy and sell respectively. If the bot doesn't want to buy/sell it will either have to use an `amount` near 0 or a very large `price` delta. `price` is a not taken literal from the AI, but is first de-normalised using: $:a price = MarketPrice * (1 +- price * MaxDelta)$ (+/- depending on if it's buy or sell). \
We omit the expire time from our interface and just assume that it should be canceled at the next candlestick. 

An standard AI architecture can easily be converted to the limit type by adding a `LimitModule` (available from `regressor.py`) to the last hidden layer (See `RNNLimit` in `regressor.py` as example). \
It supports adding additional hidden layers the the buy/sell branch if you expect to have issues with perplexity.

Predicted expire time can be supported by adding a third array with variable length, the argmax of that array is the amount of candlestick to wait before cancling.

## State (Dec 2020)
Limit orders are fully implemented and tested in our simulation subsystem (SimuMetrics), it is also supported for the Binance exchange however is currently not ready for production usage.

There are simulated tests (`tests/main.py`), as well as exchange tests for limit orders in the backend (`tests/limit.py`), but currently Kraken tests are missing.

The frontend should have support for displaying limit orders right now, however it seems to be broken at the moment.

# Reflection
The limit orders were quite an interesting task as it is a real test in technical debt because it is entirely composed of modifying existing subsystems. \
Luckily the required changes weren't to difficult and the task was quickly completed, however many non-critical bugs remained for a few weeks.

