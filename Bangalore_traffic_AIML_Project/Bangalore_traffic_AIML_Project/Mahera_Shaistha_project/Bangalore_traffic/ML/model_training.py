#!/usr/bin/env python
# coding: utf-8

# In[1]:


import pandas as pd
import numpy as np
import joblib

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

print("Libraries loaded")


# In[2]:


# Load cleaned data
file_path = "C:\\Users\\newadmin\\Downloads\\bangalore_traffic_with_death_risk (1).csv"

df = pd.read_csv(file_path)

print("Dataset loaded:", df.shape)
df.head()


# In[3]:


# Features (inputs)
features = [
    "Traffic_Volume",
    "Average Speed",
    "Travel Time Index",
    "Incident Reports",
    "Weather Conditions"
]

X = df[features]

# Target (output)
y = df["death_risk_index"]

print("X shape:", X.shape)
print("y shape:", y.shape)


# In[4]:


X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

print("Training samples:", X_train.shape)
print("Testing samples:", X_test.shape)


# In[5]:


print(X_train.dtypes)


# In[6]:


# 🔥 Convert ALL object columns to numeric

for col in df.select_dtypes(include="object").columns:
    print("Encoding:", col)
    df[col] = df[col].astype("category").cat.codes

print("✅ All categorical columns encoded")


# In[7]:


# Features (inputs)
features = [
    "Traffic_Volume",
    "Average Speed",
    "Travel Time Index",
    "Incident Reports",
    "Weather Conditions"
]

X = df[features]

# Target (output)
y = df["death_risk_index"]

print("X shape:", X.shape)
print("y shape:", y.shape)


# In[8]:


X = df[features]
y = df["death_risk_index"]

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)


# In[9]:


print(X_train.dtypes)


# In[10]:


from sklearn.ensemble import GradientBoostingRegressor

final_model = GradientBoostingRegressor(
    n_estimators=200,
    learning_rate=0.1,
    max_depth=3,
    random_state=42
)


# In[11]:


final_model.fit(X_train, y_train)


# In[12]:


print(X_train.dtypes)


# In[13]:


from sklearn.ensemble import GradientBoostingRegressor

final_model = GradientBoostingRegressor(random_state=42)


# In[14]:


final_model.fit(X_train, y_train)

print("Final model trained successfully")


# In[15]:


import joblib
import os

os.makedirs("Desktop/Bangalore_traffic/models", exist_ok=True)

joblib.dump(final_model,"Desktop/Bangalore_traffic/models/trafic_model.pkl")

print("Model saved successfully")


# In[16]:


#PREDICTION
y_pred = final_model.predict(X_test)


# In[17]:


from sklearn.metrics import mean_absolute_error, r2_score

mae = mean_absolute_error(y_test, y_pred)
r2 = r2_score(y_test, y_pred)

print("MAE:", mae)
print("R2 Score:", r2)


# In[18]:


import matplotlib.pyplot as plt

plt.figure(figsize=(8,6))

plt.scatter(range(len(y_test[:50])), y_test[:50], color='blue')
plt.scatter(range(len(y_pred[:50])), y_pred[:50], color='red')

plt.title("Actual vs Predicted Traffic Comparison")
plt.xlabel("Data Points")
plt.ylabel("Traffic Volume")

plt.legend(["Actual", "Predicted"])

plt.show()


# In[19]:


import matplotlib.pyplot as plt
import numpy as np

# Convert to numpy array if needed
actual = y_test.values[:50]
predicted = y_pred[:50]

plt.figure(figsize=(12,6))

# Plot actual
plt.plot(actual, 
         color='royalblue', 
         linewidth=3, 
         marker='o', 
         markersize=5,
         label='Actual Traffic')

# Plot predicted
plt.plot(predicted, 
         color='crimson', 
         linewidth=3, 
         linestyle='--',
         marker='s',
         markersize=5,
         label='Predicted Traffic')

# Fill error area
plt.fill_between(range(len(actual)), 
                 actual, 
                 predicted, 
                 color='gray', 
                 alpha=0.2)

plt.title("Actual vs Predicted Traffic Volume", fontsize=16, fontweight='bold')
plt.xlabel("Data Points", fontsize=12)
plt.ylabel("Traffic Volume", fontsize=12)

plt.grid(True, alpha=0.6)
plt.legend(fontsize=12)

plt.tight_layout()
plt.show()


# In[20]:


import matplotlib.pyplot as plt
import numpy as np

# Take first 30 values for clarity
actual = y_test.values[:30]
predicted = y_pred[:30]

plt.figure(figsize=(12,6))

# Actual values
plt.plot(actual, color='blue', linewidth=3, label='Actual Traffic')

# Predicted values
plt.plot(predicted, color='red', linewidth=3, linestyle='--', label='Predicted Traffic')

plt.title("Actual vs Predicted Traffic Volume", fontsize=15)
plt.xlabel("Data Points")
plt.ylabel("Traffic Volume")

plt.legend()
plt.grid(True)

plt.show()


# In[21]:


#STEP 2 MODEL TRAINING

import pandas as pd
import joblib

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.metrics import mean_absolute_error, r2_score, accuracy_score


# In[22]:


data = pd.read_csv("C:\\Users\\newadmin\\Downloads\\bangalore_traffic_with_death_risk (1).csv")

print(data.head())


# In[23]:


print(data.columns)


# In[24]:


traffic_features = [
    'Average Speed',
    'Travel Time Index',
    'Incident Reports',
    'Weather Conditions'
]

X = data[traffic_features]
y = data['Traffic_Volume']


# In[25]:


X = pd.get_dummies(X, columns=['Weather Conditions'], drop_first=True)


# In[26]:


X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)


# In[27]:


traffic_model = RandomForestRegressor(random_state=42)
traffic_model.fit(X_train, y_train)

y_pred = traffic_model.predict(X_test)

print("Traffic MAE:", mean_absolute_error(y_test, y_pred))
print("Traffic R2:", r2_score(y_test, y_pred))


# In[28]:


from sklearn.preprocessing import LabelEncoder

le_weather = LabelEncoder()

data['Weather Conditions'] = le_weather.fit_transform(data['Weather Conditions'])


# In[29]:


print(dict(zip(le_weather.classes_, le_weather.transform(le_weather.classes_))))


# In[30]:


le_area = LabelEncoder()
data['Area Name'] = le_area.fit_transform(data['Area Name'])


# In[31]:


from sklearn.preprocessing import LabelEncoder

# Encode Weather
le_weather = LabelEncoder()
data['Weather Conditions'] = le_weather.fit_transform(data['Weather Conditions'])

# If Area column exists
if 'Area' in data.columns:
    le_area = LabelEncoder()
    data['Area'] = le_area.fit_transform(data['Area'])


# In[32]:


traffic_features = [
    'Average Speed',
    'Travel Time Index',
    'Incident Reports',
    'Weather Conditions'
]

X = data[traffic_features]
y = data['Traffic_Volume']

from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)


# In[33]:


traffic_model.fit(X_train, y_train)


# In[34]:


print(X_train.head())
print(X_train.dtypes)


# In[35]:


#Evaluate Traffic Model
from sklearn.metrics import mean_absolute_error, r2_score

y_pred = traffic_model.predict(X_test)

print("Traffic MAE:", mean_absolute_error(y_test, y_pred))
print("Traffic R2:", r2_score(y_test, y_pred))


# In[36]:


#Train Accident Risk Model
data['Accident_Risk'] = pd.cut(
    data['Incident Reports'],
    bins=[-1,1,3,10],
    labels=['Low','Medium','High']
)

X = data[['Average Speed','Travel Time Index','Incident Reports','Weather Conditions']]
y = data['Accident_Risk']

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

from sklearn.ensemble import RandomForestClassifier

accident_model = RandomForestClassifier(random_state=42)
accident_model.fit(X_train, y_train)

from sklearn.metrics import accuracy_score

print("Accident Accuracy:", accuracy_score(y_test, accident_model.predict(X_test)))


# In[37]:


print(data.columns)


# In[38]:


# Clean column names
data.columns = data.columns.str.strip()  # remove extra spaces
data.columns = data.columns.str.replace(" ", "_")  # replace spaces with _
data.columns = data.columns.str.replace("-", "_")  # replace hyphens

print(data.columns)


# In[39]:


print(data.columns.tolist())


# In[40]:


for col in data.columns:
    print(f"'{col}'")


# In[41]:


y = data['death_risk_index']


# In[42]:


#Death Risk Model trained

X = data[['Average_Speed','Travel_Time_Index',
          'Incident_Reports','Traffic_Volume','Weather_Conditions']]

y = data['death_risk_index']

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

death_model = RandomForestRegressor(random_state=42)
death_model.fit(X_train, y_train)

print("Death Model R2:", r2_score(y_test, death_model.predict(X_test)))


# In[43]:


import joblib
joblib.dump(death_model, "death_model.pkl")


# In[44]:


import joblib

joblib.dump(traffic_model, "traffic_model.pkl")
joblib.dump(accident_model, "accident_model.pkl")
joblib.dump(death_model, "death_model.pkl")

print("All models saved successfully!")


# In[45]:


def predict_system(traffic_input, accident_input, death_input):
    traffic = traffic_model.predict([traffic_input])
    accident = accident_model.predict([accident_input])
    death = death_model.predict([death_input])

    return traffic[0], accident[0], death[0]


# In[46]:


traffic_input = [45, 1.2, 1, 0]  
accident_input = [45, 1.2, 1, 0]  
death_input = [45, 1.2, 1, 800, 0]  

traffic, accident, death = predict_system(
    traffic_input,
    accident_input,
    death_input
)

print("Traffic:", traffic)
print("Accident:", accident)
print("Death:", death)


# In[47]:


import requests

API_KEY = "r6cGoI3Uzjtz9axNoohhkRRTkvv80hBN"

url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=12.9716,77.5946&key=r6cGoI3Uzjtz9axNoohhkRRTkvv80hBN"

response = requests.get(url)

data = response.json()

print(data)


# In[ ]:




