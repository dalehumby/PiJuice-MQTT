"""
Get PiJuice UPS hat information and publish to MQTT for consumption by eg Node-RED and Home Assistant
"""

import argparse
import platform
import signal
import sys
import threading
from json import dumps

import paho.mqtt.client as mqtt
import yaml
from pijuice import PiJuice
from pijuice import __version__ as library_version

SERVICE_NAME = "pijuicemqtt"

parser = argparse.ArgumentParser(description="PiJuice to MQTT")
parser.add_argument(
    "-c",
    "--config",
    default="config.yaml",
    help="Configuration yaml file, defaults to `config.yaml`",
    dest="config_file",
)
args = parser.parse_args()

pijuice = PiJuice(1, 0x14)  # Instantiate PiJuice interface object
timer_thread = None


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

    # Home Assistant MQTT autoconfig
    if config["homeassistant"]["sensor"]:
        print("Publishing Home Assistant MQTT autoconfig")
        # Payload that is common to both autoconfig messages
        battery_capacity = pijuice.config.GetBatteryProfile()["data"]["capacity"]
        firmware_version = pijuice.config.GetFirmwareVersion()["data"]["version"]
        base_payload = {
            "availability_topic": f"{SERVICE_NAME}/{config['hostname']}/service",
            "payload_available": "online",
            "payload_not_available": "offline",
            "state_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "json_attributes_topic": f"{SERVICE_NAME}/{config['hostname']}/status",
            "device": {
                "identifiers": [f"{SERVICE_NAME}-{config['hostname']}"],
                "name": f"{config['hostname']}",
                "sw_version": f"Library {library_version}, Firmware {firmware_version}",
                "model": f"PiJuice {battery_capacity} mAh",
                "manufacturer": "PiSupply",
            },
        }
        # Battery charge percentage
        payload = {
            "name": f"{config['hostname']} PiJuice Battery",
            "unique_id": f"{SERVICE_NAME}-{config['hostname']}-battery",
            "value_template": "{{ value_json.batteryCharge }}",
            "device_class": "battery",
            "unit_of_measurement": "%",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/sensor/{SERVICE_NAME}-{config['hostname']}/battery/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )

        # Power/No Power binary sensor
        payload = {
            "name": f"{config['hostname']} PiJuice Power",
            "unique_id": f"{SERVICE_NAME}-{config['hostname']}-power",
            "value_template": "{{ value_json.powerInput5vIo }}",
            "payload_off": "NOT_PRESENT",
            "payload_on": "PRESENT",
            "device_class": "power",
        }
        client.publish(
            f"{config['homeassistant']['topic']}/binary_sensor/{SERVICE_NAME}-{config['hostname']}/power/config",
            dumps({**base_payload, **payload}),
            qos=1,
            retain=True,
        )


def on_exit(signum, frame):
    """
    Update MQTT services' status to `offline` and stop the timer thread.

    Called when program exit is received.
    """
    print("Exiting...")
    client.publish(
        f"{SERVICE_NAME}/{config['hostname']}/service",
        payload="offline",
        qos=1,
        retain=True,
    )
    timer_thread.cancel()
    timer_thread.join()
    sys.exit(0)


def publish_pijuice():
    """
    Publish PiJuice UPS Hat information every `period` seconds.

    See https://github.com/PiSupply/PiJuice/tree/master/Software#i2c-command-api
    """
    global timer_thread
    timer_thread = threading.Timer(config["publish_period"], publish_pijuice)
    timer_thread.start()

    status = pijuice.status.GetStatus()["data"]
    pijuice_status = {
        "batteryCharge": pijuice.status.GetChargeLevel()["data"],
        "batteryVolage": pijuice.status.GetBatteryVoltage()["data"] / 1000,
        "batteryCurrent": pijuice.status.GetBatteryCurrent()["data"] / 1000,
        "batteryTemperature": pijuice.status.GetBatteryTemperature()["data"],
        "batteryStatus": status["battery"],
        "powerInput": status["powerInput"],
        "powerInput5vIo": status["powerInput5vIo"],
        "ioVoltage": pijuice.status.GetIoVoltage()["data"] / 1000,
        "ioCurrent": pijuice.status.GetIoCurrent()["data"] / 1000,
    }
    client.publish(
        f"{SERVICE_NAME}/{config['hostname']}/status",
        dumps(pijuice_status),
    )


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
