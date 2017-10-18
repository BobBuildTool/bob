#!/bin/bash
#
# This script is intended to be used as postBuildHook. It shows a pop-up on the
# desktop after a build has finished. To activate it add it to the hooks of a
# project or in your global Bob config:
#
# ~/.config/bob/default.yaml:
# hooks:
# 	postBuildHook: /usr/lib/bob/contrib/notify.sh

HEADLINE="Bob build finished"
BODY="The build in $PWD has finished: $1"
if [[ ${XDG_CURRENT_DESKTOP:-unknown} == KDE ]] ; then
    kdialog --passivepopup "$BODY" 10 --title "$HEADLINE"
else
    notify-send -u normal -t 10000 "$HEADLINE" "$BODY"
fi
