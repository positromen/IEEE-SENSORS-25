import pandas as pd
import numpy as np
from prophet import Prophet
import plotly.express as px
import plotly.graph_objects as go
from sklearn.preprocessing import MinMaxScaler
from datetime import datetime, timedelta

def load_and_prepare_data(file_path):
    """
    Load and prepare the sensor data for analysis with validation
    """
    try:
        # Read the Excel file
        df = pd.read_excel(file_path)
        
        # Add timestamp column if not present
        if 'timestamp' not in df.columns:
            # Create timestamp starting from current date
            df['timestamp'] = pd.date_range(start=datetime.now(), periods=len(df), freq='1min')
        
        # Convert timestamp to datetime if it isn't already
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Sort by timestamp
        df = df.sort_values('timestamp')
        
        return df
    except Exception as e:
        print(f"Error loading data: {str(e)}")
        return None

def validate_data_for_prophet(df, column_name):
    """
    Validate if the data is suitable for Prophet analysis
    """
    # Check if column exists
    if column_name not in df.columns:
        return False, f"Column {column_name} not found in dataset"
    
    # Create temporary dataframe for validation
    temp_df = pd.DataFrame({
        'ds': df['timestamp'],
        'y': df[column_name]
    })
    
    # Remove NaN values
    temp_df = temp_df.dropna()
    
    # Check if we have at least 2 valid rows
    if len(temp_df) < 2:
        return False, f"Not enough valid data points for {column_name} (minimum 2 required)"
    
    return True, temp_df

def create_prophet_model(data, column_name):
    """
    Create and fit a Prophet model for a specific sensor parameter
    """
    # Validate data
    is_valid, result = validate_data_for_prophet(data, column_name)
    
    if not is_valid:
        raise ValueError(result)
    
    # Initialize and fit Prophet model
    model = Prophet(
        daily_seasonality=True,
        weekly_seasonality=True,
        changepoint_prior_scale=0.05
    )
    
    try:
        model.fit(result)
        return model
    except Exception as e:
        raise ValueError(f"Error fitting model for {column_name}: {str(e)}")

def make_predictions(model, periods=24*7):  # Default to 1 week of predictions
    """
    Make future predictions using the fitted Prophet model
    """
    try:
        future_dates = model.make_future_dataframe(periods=periods, freq='1min')
        forecast = model.predict(future_dates)
        return forecast
    except Exception as e:
        raise ValueError(f"Error making predictions: {str(e)}")

def analyze_sensor_data(file_path, parameters_to_analyze=None):
    """
    Main function to analyze sensor data and make predictions
    Returns df, results, and figures
    """
    # Load data
    df = load_and_prepare_data(file_path)
    if df is None:
        return None, None, None  # Return None for all three values
    
    if parameters_to_analyze is None:
        parameters_to_analyze = [
            'temperature', 'pressure_kPa', 'humidity',
            'accel_x', 'accel_y', 'accel_z',
            'gyro_x', 'gyro_y', 'gyro_z'
        ]
    
    results = {}
    figures = {}
    
    for parameter in parameters_to_analyze:
        try:
            print(f"\nAnalyzing {parameter}...")
            
            # Validate data first
            is_valid, result = validate_data_for_prophet(df, parameter)
            if not is_valid:
                print(f"Skipping {parameter}: {result}")
                continue
            
            # Create and fit model
            model = create_prophet_model(df, parameter)
            
            # Make predictions
            forecast = make_predictions(model)
            
            # Store results
            results[parameter] = {
                'forecast': forecast,
                'model': model,
                'current_value': df[parameter].iloc[-1],
                'mean': df[parameter].mean(),
                'std': df[parameter].std(),
                'min': df[parameter].min(),
                'max': df[parameter].max()
            }
            
            # Create visualization
            fig = go.Figure()
            
            # Add historical data
            fig.add_trace(go.Scatter(
                x=df['timestamp'],
                y=df[parameter],
                name='Historical Data',
                line=dict(color='blue')
            ))
            
            # Add predictions
            fig.add_trace(go.Scatter(
                x=forecast['ds'],
                y=forecast['yhat'],
                name='Prediction',
                line=dict(color='red')
            ))
            
            # Add prediction intervals
            fig.add_trace(go.Scatter(
                x=forecast['ds'],
                y=forecast['yhat_upper'],
                fill=None,
                mode='lines',
                line_color='rgba(255,0,0,0.2)',
                name='Upper Bound'
            ))
            
            fig.add_trace(go.Scatter(
                x=forecast['ds'],
                y=forecast['yhat_lower'],
                fill='tonexty',
                mode='lines',
                line_color='rgba(255,0,0,0.2)',
                name='Lower Bound'
            ))
            
            fig.update_layout(
                title=f'{parameter} - Historical Data and Predictions',
                xaxis_title='Time',
                yaxis_title=parameter,
                hovermode='x unified'
            )
            
            figures[parameter] = fig
            
        except Exception as e:
            print(f"Error analyzing {parameter}: {str(e)}")
            continue
    
    return df, results, figures  # Now returning the DataFrame as well

def print_analysis_summary(results):
    """
    Print a summary of the analysis results
    """
    if not results:
        print("\nNo valid results to display.")
        return
        
    print("\n\n======= Analysis Summary =======")
    for parameter, result in results.items():
        try:
            print(f"\n{parameter.upper()} Analysis:")
            print(f"Current Value: {result['current_value']:.2f}")
            print(f"Historical Mean: {result['mean']:.2f}")
            print(f"Standard Deviation: {result['std']:.2f}")
            print(f"Min Value: {result['min']:.2f}")
            print(f"Max Value: {result['max']:.2f}")
            
            # Get latest prediction
            latest_pred = result['forecast'].iloc[-1]
            print(f"Predicted Value (end of forecast): {latest_pred['yhat']:.2f}")
            print(f"Prediction Interval: [{latest_pred['yhat_lower']:.2f}, {latest_pred['yhat_upper']:.2f}]")
        except Exception as e:
            print(f"Error displaying summary for {parameter}: {str(e)}")

def main():
    # Specify your Excel file path
    file_path = 'dataset.xlsx'  # Replace with your actual file path
    
    # Specify parameters to analyze
    parameters = [
        'accel_x', 'accel_y', 'accel_z',
        'gyro_x', 'gyro_y', 'gyro_z',
        'temperature', 'pressure_kPa',
        'proximity'
    ]
    
    try:
        # Run analysis - now capturing the DataFrame
        df, results, figures = analyze_sensor_data(file_path, parameters)
        
        if results and figures:
            # Print summary
            print_analysis_summary(results)
            
            # Show interactive plots
            # for parameter, fig in figures.items():
            #    fig.show()
                
            # Extended analysis
            print("\n\nLaunching web dashboard...")
            from extended_analysis import run_extended_analysis
            run_extended_analysis(df, results, figures)
            
        else:
            print("No valid results generated. Please check your data and parameters.")
            
    except Exception as e:
        print(f"An error occurred during analysis: {str(e)}")

if __name__ == "__main__":
    main()
