"""
Get PiJuice UPS hat information and publish to MQTT for consumption by eg Node-RED and Home Assistant
"""

import argparse
import platform
import signal
import sys
import threading
from json import dumps
from pijuice import PiJuice

import paho.mqtt.client as mqtt
import yaml

SERVICE_NAME = "pijuicemqtt"
RUN_TIMERS = True

parser = argparse.ArgumentParser(description="PiJuice to MQTT")
parser.add_argument(
    "-c",
    "--config",
    default="config.yaml",
    help="Configuration yaml file, defaults to `config.yaml`",
    dest="config_file",
)
args = parser.parse_args()

pijuice = PiJuice(1, 0x14) # Instantiate PiJuice interface object

def load_config(config_file):
    """Load the configuration from config yaml file and use it to override the defaults."""
    with open(config_file, "r") as f:
        config_override = yaml.safe_load(f)

    default_config = {
        "mqtt": {
            "broker": "127.0.0.1",
            "port": 1883,
            "username": None,
            "password": None,
        },
        "homeassistant": {
            "topic": "homeassistant",
            "device_tracker": True,
            "sensor": True,
        },
        "publish_period": 30,
        "hostname": platform.node(),
    }

    config = {**default_config, **config_override}
    return config


def mqtt_on_connect(client, userdata, flags, rc):
    """Renew subscriptions and set Last Will message when connect to broker."""
    # Set up Last Will, and then set services' status to 'online'
    client.will_set(
        f"{SERVICE_NAME}/{config['hostname']}/service",
        payload="offline",
        qos=1,
        retain=True,
    )
    client.publish(
        f"{SERVICE_NAME}/{config['hostname']}/service",
        payload="online",
        qos=1,
        retain=True,
    )

    # TODO get version and battery capacity
    # pijuice.config.GetBatteryProfile()
    # pijuice.config.GetFirmwareVersion()

    # Home Assistant MQTT autoconfig
    if config["homeassistant"]["sensor"]:
        # Battery charge percentage
        payload = {
            "availability_topic": f"{SERVICE_NAME}/{config['hostname']}/service",
            "payload_available": "online",
            "payload_not_available": "offline",
            "name": f"{config['hostname']} PiJuice Battery",
            "unique_id": f"{SERVICE_NAME}-{config['hostname']}-battery",
            "state_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "value_template": "{{ value_json.batteryCharge }}",
            "device_class": "battery",
            "unit_of_measurement": "%",
            "json_attributes_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "device": {
                "identifiers": [f"{SERVICE_NAME}-{config['hostname']}"],
                "name": f"{config['hostname']}",
                "sw_version": "Software 1.7, Firmware 1.5",
                "model": "PiJuice 1000 mAh",
                "manufacturer": "PiSupply",
            },
        }

        client.publish(
            f"{config['homeassistant']['topic']}/sensor/{SERVICE_NAME}-{config['hostname']}/battery/config",
            dumps(payload),
            qos=1,
            retain=True,
        )

        # Power/No Power binary sensor
        payload = {
            "availability_topic": f"{SERVICE_NAME}/{config['hostname']}/service",
            "payload_available": "online",
            "payload_not_available": "offline",
            "name": f"{config['hostname']} PiJuice Power",
            "unique_id": f"{SERVICE_NAME}-{config['hostname']}-power",
            "state_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "value_template": "{{ value_json.powerInput5vIo }}",
            "payload_off": "NOT_PRESENT",
            "payload_on": "PRESENT",
            "device_class": "power",
            "json_attributes_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "device": {
                "identifiers": [f"{SERVICE_NAME}-{config['hostname']}"],
                "name": f"{config['hostname']}",
                "sw_version": "Software 1.7, Firmware 1.5",
                "model": "PiJuice 1000 mAh",
                "manufacturer": "PiSupply",
            },
        }
        client.publish(
            f"{config['homeassistant']['topic']}/binary_sensor/{SERVICE_NAME}-{config['hostname']}/power/config",
            dumps(payload),
            qos=1,
            retain=True,
        )


def on_exit(signum, frame):
    """
    Update MQTT services' status to `offline`

    Called when program exit is received.
    """
    global RUN_TIMERS
    print("Exiting...")
    client.publish(
        f"{SERVICE_NAME}/{config['hostname']}/service",
        payload="offline",
        qos=1,
        retain=True,
    )
    RUN_TIMERS = False
    sys.exit(0)


def publish_pijuice():
    """
    Publish PiJuice UPS Hat information every `period` seconds.

    See https://github.com/PiSupply/PiJuice/tree/master/Software#i2c-command-api
    """
    if RUN_TIMERS:
        threading.Timer(config["publish_period"], publish_pijuice).start()

    status = pijuice.status.GetStatus()["data"]
    pijuice_status = {
        "batteryCharge": pijuice.status.GetChargeLevel()["data"],
        "batteryVolage": pijuice.status.GetBatteryVoltage()["data"]/1000,
        "batteryCurrent": pijuice.status.GetBatteryCurrent()["data"]/1000,
        "batteryTemperature": pijuice.status.GetBatteryTemperature()["data"],
        "batteryStatus": status["battery"],
        "powerInput": status["powerInput"],
        "powerInput5vIo": status["powerInput5vIo"],
        "ioVoltage": pijuice.status.GetIoVoltage()["data"]/1000,
        "ioCurrent": pijuice.status.GetIoCurrent()["data"]/1000,
    }
    client.publish(
        f"{SERVICE_NAME}/{config['hostname']}/status",
        dumps(pijuice_status),
    )
    print(pijuice_status)


config = load_config(args.config_file)

if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = mqtt_on_connect
    client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
    client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    publish_pijuice()
    client.loop_forever()
