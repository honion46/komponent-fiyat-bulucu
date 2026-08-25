from .generic import UnconfiguredAdapter
class RobotHobiAdapter(UnconfiguredAdapter):
    def __init__(self): super().__init__('RobotHobi', 'https://www.robothobi.com/')
