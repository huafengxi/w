# -*- type=sh -*-
let start=`date +%s`
for i in {1..100}; do let ts=$(((start+i)*1000)); echo $ts,$RANDOM; done
