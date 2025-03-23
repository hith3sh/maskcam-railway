import cv2
import numpy as np
from PIL import Image

with open("outputs.txt") as f:
	outputs = f.read()
	print(outputs.shape)


# Parameters (you might want to adjust these)
CONF_THRESHOLD = 0.5   # minimum confidence for filtering
NMS_THRESHOLD = 0.4    # NMS threshold

# Load the original image (using cv2 for drawing)
image_cv = cv2.imread('fishbolts.jpg')
height, width = image_cv.shape[:2]

# Assuming 'outputs' is the list returned from session.run() as you showed.
# We assume outputs[0] has shape (1, num_detections, attributes)
# and that each detection is in the format:
# [x_center, y_center, bbox_width, bbox_height, conf, class1_prob, class2_prob, ...]
detections = outputs[0][0]  # shape: (num_detections, attributes)
boxes = []
confidences = []
class_ids = []

for detection in detections:
    # The first 4 values are bbox center x, center y, width, height (normalized [0,1])
    x_center, y_center, bbox_width, bbox_height = detection[:4]
    conf = detection[4]

    # Filter out low confidence detections
    if conf < CONF_THRESHOLD:
        continue

    # Determine the class with highest probability
    class_probs = detection[5:]
    class_id = np.argmax(class_probs)
    # Optionally multiply the objectness confidence with class probability
    confidence = conf * class_probs[class_id]

    # Further filter if needed
    if confidence < CONF_THRESHOLD:
        continue

    # Convert center coordinates to top-left coordinates
    left = int((x_center - bbox_width / 2) * width)
    top = int((y_center - bbox_height / 2) * height)
    w = int(bbox_width * width)
    h = int(bbox_height * height)
    
    boxes.append([left, top, w, h])
    confidences.append(float(confidence))
    class_ids.append(class_id)

# Use OpenCV's NMS to remove overlapping boxes
indices = cv2.dnn.NMSBoxes(boxes, confidences, CONF_THRESHOLD, NMS_THRESHOLD)

# Draw bounding boxes and labels on the image
if len(indices) > 0:
    for i in indices.flatten():
        left, top, w, h = boxes[i]
        # Draw rectangle
        cv2.rectangle(image_cv, (left, top), (left + w, top + h), (0, 255, 0), 2)
        label = f"ID {class_ids[i]}: {confidences[i]:.2f}"
        # Draw label background for better visibility
        (label_width, label_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(image_cv, (left, top - label_height - baseline), (left + label_width, top), (0, 255, 0), cv2.FILLED)
        cv2.putText(image_cv, label, (left, top - baseline), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

# Save the annotated image
cv2.imwrite('annotated_fishbolts.jpg', image_cv)
print("Annotated image saved as 'annotated_fishbolts.jpg'")

