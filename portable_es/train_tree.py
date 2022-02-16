# Based on AlphaZero Tree search

"""
 _______________
|
| e -f-> X -> e -f-> Y1 -> e --...
|               \           \
|                \           ...
|                 \
|                  \-f-> Y1 -> e --...
|                               \
|                                ...
|_____________________________________________

f = Model + TopK
e = Env Eval
fA = Mean(Yn: Yn+1 = O)
"""
"""
How it works:

1. Model creates distribution of favourability for each action possible
2. Model also evaluates ahead for future favourability for the topk
3. Model updates based on the end distributions (tracing back it's steps)
4. Large model is used as training for small model

Issues:
* Looking ahead will cause overfitting as they always result in same answer... :(
* Favourability in the market?
* Only 3 actions, hard to create a distribution around.
* Lookahead, how far?
"""