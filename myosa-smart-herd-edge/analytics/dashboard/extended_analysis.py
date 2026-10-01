import streamlit as st
import pandas as pd
import numpy as np
from scipy import stats
import seaborn as sns
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import plotly.graph_objects as go
import plotly.figure_factory as ff
from plotly.subplots import make_subplots
import matplotlib.pyplot as plt

def detect_anomalies(df, parameter, contamination=0.1):
    """
    Detect anomalies in the sensor data using Isolation Forest
    """
    data = df[parameter].values.reshape(-1, 1)
    iso_forest = IsolationForest(contamination=contamination, random_state=42)
    yhat = iso_forest.fit_predict(data)
    scores = iso_forest.score_samples(data)
    anomalies = yhat == -1
    return anomalies, scores

def create_correlation_analysis(df, parameters):
    """
    Perform correlation analysis between different parameters
    """
    corr_matrix = df[parameters].corr()
    fig = go.Figure(data=go.Heatmap(
        z=corr_matrix.values,
        x=corr_matrix.columns,
        y=corr_matrix.columns,
        colorscale='RdBu',
        zmin=-1,
        zmax=1
    ))
    
    fig.update_layout(
        title='Parameter Correlation Heatmap',
        width=800,
        height=800
    )
    
    return fig, corr_matrix

def create_advanced_visualizations(df, results, parameter):
    """
    Create advanced visualizations for a specific parameter
    """
    anomalies, scores = detect_anomalies(df, parameter)
    
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            'Time Series with Anomalies',
            'Parameter Distribution',
            'Anomaly Scores',
            'Rolling Statistics'
        )
    )
    
    # Add traces without displaying them immediately
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=df[parameter], name='Normal', mode='lines'),
        row=1, col=1
    )
    
    fig.add_trace(
        go.Scatter(
            x=df['timestamp'][anomalies],
            y=df[parameter][anomalies],
            name='Anomalies',
            mode='markers',
            marker=dict(color='red', size=8)
        ),
        row=1, col=1
    )
    
    hist_data = [df[parameter].values]
    fig.add_trace(
        go.Histogram(x=df[parameter], name='Distribution', nbinsx=30),
        row=1, col=2
    )
    
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=scores, name='Anomaly Score', mode='lines'),
        row=2, col=1
    )
    
    window = 20
    rolling_mean = df[parameter].rolling(window=window).mean()
    rolling_std = df[parameter].rolling(window=window).std()
    
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=rolling_mean, name=f'{window}-point Moving Average'),
        row=2, col=2
    )
    
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=rolling_std, name=f'{window}-point Standard Deviation'),
        row=2, col=2
    )
    
    fig.update_layout(height=800, width=1200, showlegend=True, title=f'Advanced Analysis for {parameter}')
    return fig

def create_streamlit_app(df, results, figures):
    """
    Create a Streamlit web application for interactive visualization
    """
    st.set_page_config(layout="wide")
    st.title('Livestock Sensor Data Analysis Dashboard')
    
    # Sidebar for parameter selection
    st.sidebar.header('Parameter Selection')
    selected_parameter = st.sidebar.selectbox(
        'Choose Parameter to Analyze',
        list(results.keys())
    )
    
    # Main content
    st.header(f'Analysis for {selected_parameter}')
    
    # Tabs for different visualizations
    tab1, tab2, tab3, tab4 = st.tabs(['Predictions', 'Advanced Analysis', 'Anomalies', 'Correlations'])
    
    with tab1:
        # Only show the Plotly figure in Streamlit, not in a separate window
        st.plotly_chart(figures[selected_parameter], use_container_width=True)
        
        st.subheader('Statistical Summary')
        stats_df = pd.DataFrame({
            'Metric': ['Current Value', 'Mean', 'Std Dev', 'Min', 'Max'],
            'Value': [
                results[selected_parameter]['current_value'],
                results[selected_parameter]['mean'],
                results[selected_parameter]['std'],
                results[selected_parameter]['min'],
                results[selected_parameter]['max']
            ]
        })
        st.table(stats_df)
    
    with tab2:
        advanced_fig = create_advanced_visualizations(df, results, selected_parameter)
        st.plotly_chart(advanced_fig, use_container_width=True)
    
    with tab3:
        st.subheader('Anomaly Detection')
        anomalies, scores = detect_anomalies(df, selected_parameter)
        anomaly_count = anomalies.sum()
        st.write(f'Number of anomalies detected: {anomaly_count}')
        
        anomaly_fig = go.Figure()
        anomaly_fig.add_trace(go.Scatter(
            x=df['timestamp'],
            y=df[selected_parameter],
            name='Normal Data',
            mode='lines'
        ))
        anomaly_fig.add_trace(go.Scatter(
            x=df['timestamp'][anomalies],
            y=df[selected_parameter][anomalies],
            name='Anomalies',
            mode='markers',
            marker=dict(color='red', size=8)
        ))
        st.plotly_chart(anomaly_fig, use_container_width=True)
    
    with tab4:
        st.subheader('Correlation Analysis')
        correlation_fig, corr_matrix = create_correlation_analysis(df, list(results.keys()))
        st.plotly_chart(correlation_fig, use_container_width=True)
        
        st.subheader('Strongest Correlations')
        correlations = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                correlations.append({
                    'Parameters': f'{corr_matrix.columns[i]} vs {corr_matrix.columns[j]}',
                    'Correlation': corr_matrix.iloc[i, j]
                })
        
        correlations_df = pd.DataFrame(correlations)
        correlations_df = correlations_df.sort_values('Correlation', key=abs, ascending=False)
        st.table(correlations_df.head(5))

def run_extended_analysis(df, results, figures):
    """
    Run the extended analysis and launch the Streamlit app
    """
    create_streamlit_app(df, results, figures)
