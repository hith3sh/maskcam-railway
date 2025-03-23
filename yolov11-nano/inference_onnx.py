import onnxruntime as ort
#import onnxruntime-gpu as ort
import numpy as np
from PIL import Image

# Load and preprocess the image
image_path = 'fishbolts.jpg'
image = Image.open(image_path).resize((640, 640))  # Resize as needed
input_data = np.array(image).astype('float32') / 255  # Normalize if required
input_data = np.transpose(input_data, (2, 0, 1))  # Change to CHW if necessary
input_data = np.expand_dims(input_data, axis=0)  # Add batch dimension

# Load the ONNX model
session = ort.InferenceSession('yolov11-weights/yolov11n-opset11.onnx', providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])

# Get model input name
input_name = session.get_inputs()[0].name

# Run inference
outputs = session.run(None, {input_name: input_data})


f = open('outputs.txt', 'w')
f.write(str(outputs))
f.close()

# Process the outputs as needed
print('done')

