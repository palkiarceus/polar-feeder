from roboflow import Roboflow

rf = Roboflow(api_key="bvpkPm89ArAa6FcrCUKX")

project = rf.workspace("Polar Bear").project("ArcticProject")
model = project.version(10, local="http://localhost:9001").model
