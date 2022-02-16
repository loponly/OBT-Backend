#!/bin/sh
pip3 install -r ./requirements.txt
pip3 install torch==1.4.0+cpu -f https://download.pytorch.org/whl/torch_stable.html

if [ -f ./evosim/requirements.txt ]
then
pip3 install -r evosim/requirements.txt
fi