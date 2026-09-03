#!/bin/bash

if [[ "$#" -lt 2 ]]; then
    echo "Usage:"
    echo "$0 start <device_id> <resolution> <timeout>"
    echo "    or"
    echo "$0 stop"
    exit 1
fi


OPERATION=$1
DEVICE_ID=$2
RESOLUTION=$3
TIMEOUT=$4

echo "- Resolution: ${RESOLUTION}"
PID_FILE_PATH="runner.pid"


case "${OPERATION}" in
    start)
        # Already running
        if [ -f ${PID_FILE_PATH} ]; then
            pid=$(cat ${PID_FILE_PATH})
            if $(ps -p ${pid} > /dev/null); then
                echo "Already running [PID: ${pid}], you can stop it and retry."
                exit 1
            fi
        fi

        ustreamer --allow-origin=\* --host=0.0.0.0 --port=8013 --device=/dev/video${DEVICE_ID} --workers=3 --drop-same-frames=30 --slowdown \
            -r ${RESOLUTION} 2>&1 &
        USTREAMER_PID=$!
        echo $USTREAMER_PID > ${PID_FILE_PATH}

        (sleep ${TIMEOUT} && kill $USTREAMER_PID) &

        if ps -p $USTREAMER_PID > /dev/null
        then
           echo "Success [PID: $USTREAMER_PID]"
           exit 0
        else
           echo "Failed to start ustreamer"
           exit 1
        fi
    ;;
    stop)
        if [ -f ${PID_FILE_PATH} ]; then
            pid=$(cat ${PID_FILE_PATH})
            kill $pid
            rm ${PID_FILE_PATH}
            echo "Stopped [PID: $pid]"
        else
            echo "No running instance found."
        fi
    ;;
esac