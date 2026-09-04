import json
import os

class ConfigLoader:
    _instance = None
    _settings = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ConfigLoader()
        return cls._instance

    def __init__(self):
        if ConfigLoader._instance is not None:
            raise Exception("This class is a singleton!")
        else:
            ConfigLoader._instance = self
            self.load_config()

    def load_config(self):
        # src/utils/ -> src/ -> root (3 levels up)
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(base_path, 'config', 'settings.json')
        
        default_settings = {
            "appearance": {
                "background_color": "#000000",
                "rail_color": "#FFFFFF",
                "section_color": "#FFFF00",
                "rail_width": 100,
                "oht_color_idle": "#0000FF",
                "oht_color_assigned": "#00FF00",
                "oht_color_loaded": "#FF0000",
                "oht_color_moving": "#FFA500",
                "eq_color": "#FF69B4"
            }
        }

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    self._settings = json.load(f)
                print(f"Loaded config from {config_path}")
            except Exception as e:
                print(f"Error loading config: {e}")
                self._settings = default_settings
        else:
            print("Config file not found, using defaults.")
            self._settings = default_settings

    def get(self, section, key, default=None):
        return self._settings.get(section, {}).get(key, default)