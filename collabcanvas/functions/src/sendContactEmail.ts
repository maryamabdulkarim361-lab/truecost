import {safeLog} from './safeDiagnostics';
import {allowedOrigins, checkOrigin} from './functionSecurity';
/**
 * Cloud Function to send contact form emails
 * Uses Resend API to send to all team members
 */

import * as functions from 'firebase-functions';
import { Resend } from 'resend';

// CORS configuration
const corsHeaders = {
  'Access-Control-Allow-Origin': typeof allowedOrigins()[0] === 'string' ? String(allowedOrigins()[0]) : '',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

// All 6 team member emails
const TEAM_EMAILS = [
  'ycorcos26@gmail.com',
  'ivanovkalin7@gmail.com',
  'kishorkashid99@gmail.com',
  'sainatha.yatham@gmail.com',
  'atharva.sardar02@gmail.com',
  'ankitrijal2054@gmail.com',
];

interface ContactFormData {
  email: string;
  phone?: string;
  subject: string;
  message: string;
}

export const sendContactEmail = functions
  .region('us-central1')
  .runWith({
    secrets: ['RESEND_API_KEY'],
  })
  .https.onRequest(async (req, res) => {
    try { checkOrigin(req.headers.origin); } catch { res.status(403).json({error:'Origin not allowed'}); return; }
    const headers = {...corsHeaders, 'Access-Control-Allow-Origin': req.headers.origin || ''};
    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
      res.set(headers);
      res.status(204).send('');
      return;
    }

    res.set(headers);

    if (req.method !== 'POST') {
      res.status(405).json({ success: false, error: 'Method not allowed' });
      return;
    }

    try {
      const { email, phone, subject, message } = req.body as ContactFormData;

      // Validate required fields
      if (!email || !subject || !message) {
        res.status(400).json({
          success: false,
          error: 'Missing required fields: email, subject, message'
        });
        return;
      }

      // Get Resend API key from Firebase secrets
      const resendApiKey = process.env.RESEND_API_KEY;

      if (!resendApiKey) {
        safeLog('sendContactEmail.error', 'RESEND_API_KEY secret not configured');
        res.status(500).json({
          success: false,
          error: 'Email service not configured'
        });
        return;
      }

      const resend = new Resend(resendApiKey);

      // Email content
      const htmlContent = `
        <h2>New Contact Form Submission</h2>
        <p><strong>From:</strong> ${email}</p>
        ${phone ? `<p><strong>Phone:</strong> ${phone}</p>` : ''}
        <p><strong>Subject:</strong> ${subject}</p>
        <hr />
        <h3>Message:</h3>
        <p>${message.replace(/\n/g, '<br>')}</p>
        <hr />
        <p style="color: #666; font-size: 12px;">
          Sent from gettruecost.com contact form
        </p>
      `;

      const textContent = `
New Contact Form Submission

From: ${email}
${phone ? `Phone: ${phone}` : ''}
Subject: ${subject}

Message:
${message}

---
Sent from gettruecost.com contact form
      `.trim();

      // Send email to all team members
      await resend.emails.send({
        from: 'onboarding@resend.dev', // Use verified domain in production: 'TrueCost <contact@gettruecost.com>'
        to: TEAM_EMAILS,
        replyTo: email,
        subject: `[TrueCost Contact] ${subject}`,
        html: htmlContent,
        text: textContent,
      });

      safeLog('sendContactEmail.log', 'Contact email sent successfully to all team members', { from: email, subject });

      res.status(200).json({ success: true, message: 'Email sent successfully' });
    } catch (error) {
      safeLog('sendContactEmail.error', 'Error sending contact email:', error);
      res.status(500).json({
        success: false,
        error: 'Failed to send email. Please try again later.'
      });
    }
  });
