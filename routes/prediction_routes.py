from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from services.prediction_service import predict_risk_for_student
from models.predictor import Prediction

prediction_bp = Blueprint('prediction', __name__)


@prediction_bp.route('/api/predictions/refresh', methods=['POST'])
@login_required
def refresh_predictions():
    try:
        results = predict_risk_for_student(current_user.id)
        return jsonify({'success': True, 'predictions': results})
    except FileNotFoundError as error:
        return jsonify({'success': False, 'error': str(error)}), 500


@prediction_bp.route('/api/predictions')
@login_required
def get_predictions():
    rows = Prediction.get_for_student(current_user.id)
    predictions = [dict(row) for row in rows]
    return jsonify({'predictions': predictions})
